"""Process commands in natural language."""

# This would have been interesting to be done with actual training
# of a large corpus of sentence -> query and creating a decision
# tree based on that.
# However because the term list is limited to a very specific set
# of keywords, doing this heuristically is very likely a LOT more
# accurate.
# It might be curios to see how a trained classifier would do compared
# to the heuristic one. Or have one as a fall-back.

from __future__ import print_function

import re
import getpass
import logging
import argparse
import datetime
import functools

from core import load_configuration

import spacy.en
import dateparser
from dateutil.relativedelta import relativedelta

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
    "contain", "in", "isin",
}
negated_partials = {
    "notin", "notlike", "isnotin",
}
exacts = {
    "is", "exactly", "exact", "equals", "same", "identical", "specific",
    "'ve", "have",
}
negated_exacts = {
    "isnot",
}
startings = {
    "starts", "start", "begin", "begins"
}
endings = {
    "ends", "end"
}
mes = {
    "me", "my", "i", "tome",
}
change_modifiers = {
    "changed", "change", "modified",
}
on_date = {
    "on",
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
REPL = {
    "is in": "isin",
    "not in": "notin",
    "is not": "isnot",
    "not like": "notlike",
    "is not in": "isnotin",
    "to me": "tome",
    "open and closed": "all",
    "opened and closed": "all",
}


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
        # These are FAR from being accurate, but they get the
        # job done most of the time.
        # XXX It might be better to extrapolate the actual
        # XXX format/tenses here from a dictionary.

        # Some token can have plural form so add them as wel.
        TRANSLATE_STATUS_TOKENS[_status_token] = _status_token
        if _status_token.endswith("s"):
            TRANSLATE_STATUS_TOKENS[_status_token[:-1]] = _status_token
        else:
            TRANSLATE_STATUS_TOKENS[_status_token + "s"] = _status_token
        # Add some of the past tense
        if _status_token.endswith("ed"):
            TRANSLATE_STATUS_TOKENS[_status_token[:-2]] = _status_token
            TRANSLATE_STATUS_TOKENS[_status_token[:-2] + "ing"] = _status_token
            TRANSLATE_STATUS_TOKENS[_status_token[:-1]] = _status_token
        # Gerund formats
        if _status_token.endswith("e"):
            TRANSLATE_STATUS_TOKENS[_status_token + "d"] = _status_token
            TRANSLATE_STATUS_TOKENS[_status_token[:-1] + "ing"] = _status_token
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

RESOLUTIONS = CONF.get("trac", "resolutions").split(",")

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
        logger.debug("Skipped Filter (level:%s): %s (%s)", level, token,
                     curr_filter)
        return
    # If this is the first call, initialize the current
    # filter. By default the filter is not negated.
    if curr_filter is None:
        curr_filter = {"not": False, "list": False, "status_tokens": set(),
                       "extra_tokens": []}
    original_curr_filter = {
        "name": curr_filter.get("name", None),
        "op": curr_filter.get("op", None),
        "val": curr_filter.get("val", None),
    }
    full = "name" in curr_filter and "op" in curr_filter and "val" in curr_filter
    processed = True
    if token.orth_ in KNOWN and "name" not in curr_filter:
        # We know this filter type
        curr_filter["name"] = KNOWN[token.orth_]
    elif token.lower_ in partials and "op" not in curr_filter:
        curr_filter["op"] = "=~"
    elif token.lower_ in negated_partials and "op" not in curr_filter:
        curr_filter["op"] = "=!~"
        curr_filter["not"] = True
        # Any following token in the tree should be
        # negated
        negates = True
    elif token.lower_ in exacts and "op" not in curr_filter:
        curr_filter["op"] = "="
    elif token.lower_ in negated_exacts and "op" not in curr_filter:
        curr_filter["op"] = "=!"
        curr_filter["not"] = True
        # Any following token in the tree should be
        # negated
        negates = True
    elif token.lower_ in negations and not curr_filter["not"]:
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
    elif token.lower_ in RESOLUTIONS and not full:
        # The user specified the exact resolution.
        curr_filter["name"] = "resolution"
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
    elif token.orth_ in on_date and level == 0:
        curr_filter["name"] = "on"
    elif token.orth_ in start_date and level == 0:
        curr_filter["name"] = "from"
    elif token.orth_ in end_date and level == 0:
        curr_filter["name"] = "to"
    else:
        curr_filter["extra_tokens"].append(token)
        processed = False

    if token.orth_ in TRANSLATE_STATUS_TOKENS:
        curr_filter["status_tokens"].add(token)

    if processed:
        already_processed.append(token)
        if negates and "!" not in curr_filter.get("op", "!"):
            curr_filter["op"] = curr_filter["op"].replace("=", "=!")
    for k, v in original_curr_filter.items():
        if curr_filter.get(k, None) != v:
            curr_filter[k + "_"] = token
    logger.debug("Get Filter (level:%s): %s (%s)", level, token, curr_filter)
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
    ago_tokens = []
    number = None
    dtype = None
    ago = False
    for i in tokens:
        if i.pos_ == "CONJ":
            # We are heading into a different command
            # or filter, stop.
            break
        if i.pos_ == "DET":
            continue
        value = str(NUMBERS.get(i.lower_, i.lower_))
        stokens.append(value)
        rtokens.append(i)

        if number is None:
            try:
                number = str(int(value))
                ago_tokens.append(i)
                continue
            except (TypeError, ValueError):
                pass
        if i.orth_ in ("ago", "last", "past") and not ago:
            ago = True
            ago_tokens.append(i)
            continue
        if dtype is None:
            ago_tokens.append(i)
            dtype = i.orth_

    if ago:
        if number is None:
            # Also known as William Riker
            number = "1"
        logger.debug("Trying to extract date from: %s %s", number, dtype)
        result = dateparser.parse("%s %s ago" % (number, dtype))
        if result is not None:
            logger.debug("Extracted date %s from %s", result, stokens)
            already_processed.extend(ago_tokens)
            return result

    if len(stokens) == 1 and number is not None:
        # We cannot deduce the date from a single number
        # but dateparser thinks he can.
        return

    logger.debug("Trying to extract date from: %s", stokens)
    result = dateparser.parse(" ".join(stokens))
    if result is not None:
        logger.debug("Extracted date %s from %s", result, stokens)
        already_processed.extend(rtokens)
        return result

    stokens.reverse()
    logger.debug("Trying to extract date from: %s", stokens)
    result = dateparser.parse(" ".join(stokens))
    if result is not None:
        logger.debug("Extracted date %s from %s", result, stokens)
        already_processed.extend(rtokens)
        return result


def natural_to_query(query, user):
    trac_query = []
    logger.info("Processing natural query: %r", query)

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
    for i, j in REPL.items():
        query = query.replace(i, j)
    logger.debug("Replaced expressions %r", query)

    now = datetime.datetime.utcnow()

    fixed_interpolation = {
        "user": user,
        "month": now.strftime("%B"),
        "year": now.strftime("%Y"),
        "day": now.strftime("%d"),
        "last_month": (now - relativedelta(months=1)).strftime("%B"),
        "last_month_year":
            (now - relativedelta(months=1)).strftime("%Y"),
        "last_year": (now - relativedelta(years=1)).strftime("%Y"),
        "yesterday": (now - relativedelta(days=1)).strftime("%d"),
    }

    # If we process and accept a token as part
    # of a filter while going through the
    # semantic tree, store it here, so we don't
    # wrongly reuse it in another filter.
    already_processed = []
    tokens = nlp(query.decode("utf8"))
    start_time = None
    end_time = None
    changed = False
    status_provided = False
    all_provided = False
    resolution_provided = False
    for token in tokens:
        logger.debug("Checking token: %s", token)
        if token in already_processed:
            logger.debug("Already processed: %s", token)
            continue

        if token.lower_ in "all":
            all_provided = True

        if token.lower_ in change_modifiers:
            changed = True
            already_processed.append(token)
            continue

        if token.orth_ in FIXED_QUERIES_REVERSED:
            # The query is fixed by the config file for this keyword.
            # Also check if is negated
            fixed_query = CONF.get("fixed_queries",
                                   FIXED_QUERIES_REVERSED[token.orth_])
            fixed_query = fixed_query % fixed_interpolation
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
            possible_time = parse_date(f["extra_tokens"], new_already_processed)
            assert possible_time is not None
            if (start_time is None and end_time is None and
                    f.get("name", "") == "on"):
                start_time, end_time = possible_time, possible_time
            elif start_time is None and ("name" not in f or
                                       f["name"] == "from"):
                start_time = possible_time
            elif f.get("name", "") == "to" and end_time is None:
                end_time = possible_time
            else:
                raise AssertionError()
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
            already_processed.append(f["name_"])
            already_processed.append(f["op_"])
            already_processed.append(f["val_"])
            processed = True
        except KeyError:
            pass
        if processed and f["name"] == "status":
            status_provided = True
        elif processed and f["name"] == "resolution":
            resolution_provided = True
        # Check if any of the gathered status tokens match
        # the known ones, and add a status filer.
        if f.get("name", "") != "status" or not processed:
            status_tokens = frozenset([TRANSLATE_STATUS_TOKENS[t.orth_]
                                       for t in f["status_tokens"]])
            try:
                status = TOKENIZED_STATUSES[status_tokens]
                if f["not"]:
                    trac_query.append("status=!" + status)
                else:
                    trac_query.append("status=" + status)
                already_processed.extend(f["status_tokens"])
                status_provided = True
                processed = True
            except KeyError:
                pass

        # Not always right, but good enough.
        if token.lower_ in ("my", "tome") and not processed:
            if is_negated(token):
                trac_query.append("owner=!" + user)
            else:
                trac_query.append("owner=" + user)

    # We could theoretically have more than two dates.
    # For example trying to filter both by change time and
    # opening time. But that's getting too complex for now.
    if start_time is not None or end_time is not None:
        end_time = "" if end_time is None else end_time.strftime("%Y-%m-%d")
        start_time = "" if start_time is None else start_time.strftime("%Y-%m-%d")
        filter_value = "time"
        if changed:
            filter_value = "changetime"
        trac_query.append("%s=%s..%s" % (
            filter_value,
            start_time,
            end_time,
        ))

    if not status_provided and not all_provided and not resolution_provided:
        trac_query.append("status=!closed")

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
