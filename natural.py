"""Process commands in natural language."""
from __future__ import print_function

import os
import re
import getpass
import logging
import argparse
import datetime
import functools

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import spacy.en
import dateparser


def load_configuration():
    defaults = {
        "trac": {
            "host": "",
            "user": "",
            "password": "",
            "limit": "35",
            "components": "",
            "priorities": "lowest,low,normal,high,highest",
            "types": "defect,enhancement,task",
            "extra_fields": "",
            "statuses": ""
        },
        "fixed_queries": {},
        "slack": {
            "token": "",
            "endpoint": "/trac-slack"
        },
        "logging": {
            "file": "/var/log/trac-slack.log",
            "level": "INFO",
            "sentry": "",
            "user": "www-data",
        }
    }
    conf = configparser.ConfigParser()
    # Load in default values.
    for section, values in defaults.items():
        conf.add_section(section)
        for option, value in values.items():
            conf.set(section, option, value)

    if os.path.exists("/etc/trac-slack.conf"):
        conf.read("/etc/trac-slack.conf")
    return conf


CONF = load_configuration()
nlp = spacy.en.English()
logger = logging.getLogger("trac-slack-nlp")

negations = {
    "no", "not", "n't", "never", "none", "unlike", "different",
    "dissimilar", "unequal",
}
partials = {
    "has", "like", "contains", "alike", "related", "close", "matching",
    "near", "matching", "akin", "relating", "resembling", "fuzzy",
    "contain",
}
exacts = {
    "is", "exactly", "exact", "equals", "same", "identical", "specific", "'m",
    "am", "are"
}
startings = {
    "starts", "start", "begin", "begins"
}
endings = {
    "ends", "end"
}
mes = {
    "me", "my", "i",
}
start_date = {
    "from", "since", "after",
}
end_date = {
    "to", "before",
}

match_re = re.compile(r"""
    (?<=\s)(('.*?')|(".*?")) |
    ^(('.*?')|(".*?"))
""", re.X)
date_re = re.compile(r"""
(
    (?:\s|^)
    (?:\d{4})|(?:\d{2})   # Group 1 (2 or 4 digits)
)
-
(\d{2})                   # Group 2 (2 digits)
-
(
    (?:\d{4})|(?:\d{2})   # Group 3 (2 or 4 digits)
    (:?\s|$)
)
""", re.X | re.M)


def _is_something(tok, checks=None):
    if checks is None:
        return False
    for dep in list(tok.lefts) + list(tok.rights):
        if dep.lower_ in checks:
            return True
    return False


is_negated = functools.partial(_is_something, checks=negations)
is_partial = functools.partial(_is_something, checks=partials)
is_exact = functools.partial(_is_something, checks=exacts)

UNIQUE_C = "YlaQbS0ydqe8"
UNIQUE_M = "wbsy0cnev3av"
UNIQUE_F = "57eaba821d58"

# Order is important here.
PRIORITIES = CONF.get("trac", "priorities").split(",")

TICKET_TYPES = {_type: _type for _type in CONF.get("trac", "types").split(",")}
TICKET_TYPES.update({_type + "s": _type for _type in TICKET_TYPES})

# XXX We should tokenize these the same way to tokenize
# XXX status options.
_COMPONENTS = CONF.get("trac", "components").split(",")
COMPONENTS = {_comp.lower(): str(i) + UNIQUE_C
              for i, _comp in enumerate(_COMPONENTS)}
REVERSED_COMPONENTS = {str(i) + UNIQUE_C: _comp
                       for i, _comp in enumerate(_COMPONENTS)}

STATUSES = CONF.get("trac", "statuses").split(",")
# Tokenize statuses as these can be compound words.
# Stores a maping of a frozen set of tokens to the
# actual status.
TOKENIZED_STATUSES = {}
TRANSLATE_STATUS_TOKENS = {}
for _status in STATUSES:
    _status_tokens = set()
    for _status_token in _status.split("_"):
        _status_tokens.add(_status_token)

        # Add the status token to the translation dictionary.
        # Some token can have plural form so add them as wel.
        TRANSLATE_STATUS_TOKENS[_status_token] = _status_token
        if _status_token.endswith("s"):
            TRANSLATE_STATUS_TOKENS[_status_token[:-1]] = _status_token
        else:
            TRANSLATE_STATUS_TOKENS[_status_token + "s"] = _status_token
    TOKENIZED_STATUSES[frozenset(_status_tokens)] = _status

FIXED_QUERIES = {
    _fixed: str(i) + UNIQUE_F
    for i, _fixed in enumerate(CONF.options("fixed_queries"))
    }
FIXED_QUERIES_REVERSED = {
    str(i) + UNIQUE_F: _fixed
    for i, _fixed in enumerate(CONF.options("fixed_queries"))
    }

# Known filters names
KNOWN = {
    "cc": "cc",
    "status": "status",
    "component": "component",
    "components": "component",
    "title": "summary",
    "summary": "summary",
    "text": "description",
    "description": "description",
    "owner": "owner",
    "reporter": "reporter",
    "reported": "reporter",
    "keywords": "keywords",
    "keyword": "keywords",
    "severity": "priority",
    "priority": "priority",
    "milestone": "milestone",
    "resolution": "resolution",
}
# Add any custom trac fields to the known list.
KNOWN.update({field.lower(): field
              for field in CONF.get("trac", "extra_fields").split(",")})
NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def get_filter(token, texts, user, already_processed, curr_filter=None,
               negates=False, level=0):
    if token in already_processed:
        return

    # If this is the first call, initialize the current
    # filter. By default the filter is not negated.
    if curr_filter is None:
        curr_filter = {"not": False, "list": False, "status_tokens": set(),
                       "extra_tokens": []}

    full = "name" in curr_filter and "op" in curr_filter and "val" in curr_filter
    processed = True
    if token.orth_ in KNOWN and "name" not in curr_filter:
        # We know this filter type
        curr_filter["name"] = KNOWN[token.orth_]
    elif token.lower_ in partials and "op" not in curr_filter:
        curr_filter["op"] = "=~"
    elif token.lower_ in exacts and "op" not in curr_filter:
        curr_filter["op"] = "="
    elif token.lower_ in negations:
        curr_filter["not"] = True
        # Any following token in the tree should be
        # negated
        negates = True
    elif token.lower_ in startings and "op" not in curr_filter:
        curr_filter["op"] = "=^"
    elif token.lower_ in endings and "op" not in curr_filter:
        curr_filter["op"] = "=$"
    elif token.orth_ in REVERSED_COMPONENTS and not full:
        # The user made it easy, this is
        # a component filter.
        curr_filter["name"] = "component"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = REVERSED_COMPONENTS[token.orth_]
    elif token.orth_ in texts:
        # The user made it easy, this is a quoted
        # string, so it's the value.
        curr_filter["val"] = texts[token.orth_]
        # If this is just a string, then it's likely
        # the user wants to search the description.
        # XXX Not really sure if this would be best
        # if "name" not in curr_filter:
        #     curr_filter["name"] = "description"
        #     curr_filter["op"] = "=~"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
    elif token.lower_ in PRIORITIES and not full:
        # We already know the list of priorities
        # and this is an exact match.
        curr_filter["name"] = "priority"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = token.orth_
    elif token.lower_ in TICKET_TYPES and not full:
        # We already know the list of ticket types
        # and this is an exact match.
        curr_filter["name"] = "type"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = TICKET_TYPES[token.orth_]
    elif (token.orth_ == "higher" and
                  curr_filter.get("name", "") == "priority"
          and "val" in curr_filter):
        # The user want all priorities higher than
        # the specified one.
        curr_val = curr_filter["val"]
        values = PRIORITIES[PRIORITIES.index(curr_val):]
        curr_filter["list"] = True
        curr_filter["val"] = values
    elif (token.orth_ == "lower" and
            curr_filter.get("name", "") == "priority"
            and "val" in curr_filter):
        # The user want all priorities lower than
        # the specified one.
        curr_val = curr_filter["val"]
        values = PRIORITIES[:PRIORITIES.index(curr_val) + 1]
        curr_filter["list"] = True
        curr_filter["val"] = values
    elif token.lower_ in STATUSES and not full:
        # The user specified the exact status.
        curr_filter["name"] = "status"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = token.orth_
    elif (token.lower_ in mes and not full and
                  "val" not in curr_filter):
        curr_filter["val"] = user
    elif ("name" in curr_filter and "op" in curr_filter and
                  "val" not in curr_filter and
                  token.pos_ not in ("ADP", "DET", "PUNCT", "CONJ", "DET")
          ):
        # We already have the other two,
        # this is likely the value.
        # XXX Risky assumption.
        curr_filter["val"] = token.orth_
    elif token.orth_ in start_date and level == 0:
        curr_filter["name"] = "from"
    elif token.orth_ in end_date and level == 0:
        curr_filter["name"] = "to"
    else:
        curr_filter["extra_tokens"].append(token)
        processed = False

    if token.orth_ in TRANSLATE_STATUS_TOKENS:
        curr_filter["status_tokens"].add(TRANSLATE_STATUS_TOKENS[token.orth_])

    if processed:
        already_processed.append(token)
        if negates and "!" not in curr_filter.get("op", "!"):
            curr_filter["op"] = curr_filter["op"].replace("=", "=!")

    logger.debug("Get Filter: %s (%s)", token, curr_filter)
    # Go through the semantic tree and figure out the
    # rest of the filter values.
    for child in token.children:
        get_filter(child, texts, user, already_processed, curr_filter,
                   negates=negates, level=level+1)
    return curr_filter


def parse_date(tokens, already_processed):
    # Try to order the tokens
    stokens = []
    rtokens = []
    number = None
    dtype = None
    for i in tokens:
        if i.pos_ == "CONJ":
            # We are heading into a different command
            # or filter, stop.
            break
        if i.pos_ == "DET":
            continue
        value = NUMBERS.get(i.lower_, i.lower_)
        stokens.append(value)
        rtokens.append(i)

        try:
            number = int(value)
            continue
        except (TypeError, ValueError):
            pass
        if i.orth_ == "ago":
            continue
        dtype = i.orth_

    logger.debug("Trying to extract date from: %s %s", number, dtype)
    result = dateparser.parse("%s %s ago" % (number, dtype))
    if result is not None:
        logger.debug("Extracted date %s from %s", result, stokens)
        already_processed.extend(rtokens)
        return result

    logger.debug("Trying to extract date from: %s", stokens)
    result = dateparser.parse(" ".join(stokens))
    if result is not None:
        logger.debug("Extracted date %s from %s", result, stokens)
        already_processed.extend(rtokens)
        return result


def natural_to_query(query, user):
    trac_query = []
    logger.info("Processing query: %r", query)

    # Replace quoted string with unique ids, as the
    # user clearly wants us to interpret them as
    # single tokens
    texts = {}
    inc = 0
    while True:
        repl = str(inc) + UNIQUE_M
        try:
            text = match_re.search(query).group()
        except AttributeError:
            break
        texts[repl] = text.strip('"\'')
        query = match_re.sub(repl, query, 1)
        inc += 1
    query = query.lower()
    logger.debug("Found text search: %s", texts)
    logger.debug("Query: %s", query)
    query = date_re.sub(r" \1/\2/\3 ", query)
    logger.debug("Replace dates: %s", query)
    # Replace any fixed keywords provided in the
    # config file.
    for i, j in FIXED_QUERIES.items():
        query = query.replace(i, j)
    logger.debug("Replaced fixed queries %r", query)
    # Replace component names with unique ids
    # as those are known to us already
    for i, j in COMPONENTS.items():
        query = query.replace(i, j)
    logger.debug("Replaced components %r", query)

    # If we process and accept a token as part
    # of a filter while going through the
    # semantic tree, store it here, so we don't
    # wrongly reuse it in another filter.
    already_processed = []
    tokens = nlp(query.decode("utf8"))
    start_time = None
    end_time = None
    for token in tokens:
        logger.debug("Checking token: %s", token)
        if token in already_processed:
            logger.debug("Already processed: %s", token)
            continue

        if token.orth_ in FIXED_QUERIES_REVERSED:
            # The query is fixed by the config file for this keyword.
            # Also check if is negated
            fixed_query = CONF.get("fixed_queries",
                                   FIXED_QUERIES_REVERSED[token.orth_])
            if is_negated(token):
                for fixed in fixed_query.split("&"):
                    if "=!" in fixed:
                        fixed = fixed.replace("=!", "=")
                    else:
                        fixed = fixed.replace("=", "=!")
                    trac_query.append(fixed)
            else:
                trac_query.append(fixed_query)
            already_processed.append(token)
            continue

        processed = False

        # Try to extract a filter by going trough the
        # semantic tree, starting from this token, while
        # ignoring any already processed tokens.
        new_already_processed = list(already_processed)
        f = get_filter(token, texts, user, new_already_processed)
        logger.debug("Resulting filter: %s", f)

        try:
            assert f["name"] == "from"
            assert start_time is None
            start_time = parse_date(f["extra_tokens"], already_processed)
            assert start_time is not None
            already_processed = new_already_processed
            continue
        except (KeyError, AssertionError):
            pass

        try:
            assert f["name"] == "to"
            assert end_time is None
            end_time = parse_date(f["extra_tokens"], already_processed)
            assert end_time is not None
            already_processed = new_already_processed
            continue
        except (KeyError, AssertionError):
            pass

        try:
            # if f["not"]:
            #     f["op"] = f["op"].replace("=", "=!")
            if f["val"] == "me":
                f["val"] = user
            if not f["list"]:
                f["val"] = [f["val"]]
            for val in f["val"]:
                trac_query.append(f["name"] + f["op"] + val)
            # We accepted the filter, update the already
            # processed list.
            already_processed = new_already_processed
            processed = True
        except KeyError:
            pass

        # Check if any of the gathered status tokens match
        # the known ones, and add a status filer.
        if f.get("name", "") != "status" or not processed:
            try:
                status = TOKENIZED_STATUSES[frozenset(f["status_tokens"])]
                if f["not"]:
                    trac_query.append("status=!" + status)
                else:
                    trac_query.append("status=" + status)
                processed = True
            except KeyError:
                pass

        # Not always right, but good enough.
        if token.lower_ in mes and not processed:
            if is_negated(token):
                trac_query.append("owner=!" + user)
            else:
                trac_query.append("owner=" + user)

    if start_time is not None or end_time is not None:
        end_time = end_time or datetime.datetime.utcnow()
        start_time = start_time or datetime.datetime.utcnow()
        trac_query.append("time=%s..%s" % (
            start_time.strftime("%Y-%m-%d"),
            end_time.strftime("%Y-%m-%d"),
        ))

    logger.info("Created query: %s", trac_query)
    return "&".join(trac_query)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("query", help="Natural language to convert to "
                                      "Trac query.")
    parser.add_argument("-d", "--debug", action="store_true", default=False,
                        help="Enable debug")
    parser.add_argument("-i", "--info", action="store_true", default=False,
                        help="Enable info")
    args = parser.parse_args()
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(message)s')
    )

    logger.setLevel(logging.DEBUG)
    if args.debug:
        sh.setLevel(logging.DEBUG)
    elif args.info:
        sh.setLevel(logging.INFO)
    else:
        sh.setLevel(logging.ERROR)
    logger.addHandler(sh)

    query = args.query.lower()
    if query == "interactive":
        while True:
            query = raw_input("Enter Query: ").lower()
            if query == "stop":
                break
            print(natural_to_query(query, getpass.getuser()))
    else:
        print(natural_to_query(query, getpass.getuser()))


if __name__ == "__main__":
    main()
