"""Process commands in natural language."""
from __future__ import print_function

import os
import re
import getpass
import logging
import argparse
import functools
try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import spacy.en


def load_configuration():
    defaults = {
        "trac": {
            "host": "",
            "user": "",
            "password": "",
            "limit": "35",
            "components": "",
            "priorities": "lowest,low,normal,high,highest",
            "types": "bug,feature,task",
            "extra_fields": "",
            "statuses": ""
        },
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

match_re = re.compile(r'(\'.*?\')|(".*?")')


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
UNIQUE_M = "WBsy0cNev3aV"

# Order is important here.
priorities = CONF.get("trac", "priorities").split(",")

ticket_types = {_type: _type for _type in CONF.get("trac", "types").split(",")}
ticket_types.update({_type + "s": _type for _type in ticket_types})

# XXX We should tokenize these the same way to tokenize
# XXX status options.
_components = CONF.get("trac", "components").split(",")
components = {_comp.lower(): str(i) + UNIQUE_C
              for i, _comp in enumerate(_components)}
reversed_components = {str(i) + UNIQUE_C: _comp
                       for i, _comp in enumerate(_components)}

statuses = CONF.get("trac", "statuses").split(",")
# Tokenize statuses as these can be compound words.
# Stores a maping of a frozen set of tokens to the
# actual status.
tokenized_statuses = {}
translate_status_tokens = {}
for _status in statuses:
    _status_tokens = set()
    for _status_token in _status.split("_"):
        _status_tokens.add(_status_token)

        # Add the status token to the translation dictionary.
        # Some token can have plural form so add them as wel.
        translate_status_tokens[_status_token] = _status_token
        if _status_token.endswith("s"):
            translate_status_tokens[_status_token[:-1]] = _status_token
        else:
            translate_status_tokens[_status_token + "s"] = _status_token
    tokenized_statuses[frozenset(_status_tokens)] = _status

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
}
# Add any custom trac fields to the known list.
KNOWN.update({field.lower(): field
              for field in CONF.get("trac", "extra_fields").split(",")})


def get_filter(token, texts, user, already_processed, curr_filter=None):
    if token in already_processed:
        return

    # If this is the first call, initialize the current
    # filter. By default the filter is not negated.
    if curr_filter is None:
        curr_filter = {"not": False, "list": False, "status_tokens": set()}

    logger.debug("Get Filter: %s (%s)", token, curr_filter)
    processed = True
    if token.orth_ in KNOWN:
        # We know this filter type
        curr_filter["name"] = KNOWN[token.orth_]
    elif token.lower_ in partials:
        curr_filter["op"] = "=~"
    elif token.lower_ in exacts:
        curr_filter["op"] = "="
    elif token.lower_ in negations:
        curr_filter["not"] = True
    elif token.lower_ in startings:
        curr_filter["op"] = "=^"
    elif token.lower_ in endings:
        curr_filter["op"] = "=$"
    elif token.orth_ in reversed_components:
        # The user made it easy, this is
        # a component filter.
        curr_filter["name"] = "component"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = reversed_components[token.orth_]
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
    elif token.lower_ in priorities:
        # We already know the list of priorities
        # and this is an exact match.
        curr_filter["name"] = "priority"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = token.orth_
    elif token.lower_ in ticket_types:
        # We already know the list of ticket types
        # and this is an exact match.
        curr_filter["name"] = "type"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = ticket_types[token.orth_]
    elif (token.orth_ == "higher" and
              curr_filter.get("name", "") == "priority"
              and "val" in curr_filter):
        # The user want all priorities higher than
        # the specified one.
        curr_val = curr_filter["val"]
        values = priorities[priorities.index(curr_val):]
        curr_filter["list"] = True
        curr_filter["val"] = values
    elif (token.orth_ == "lower" and
            curr_filter.get("name", "") == "priority"
            and "val" in curr_filter):
        # The user want all priorities lower than
        # the specified one.
        curr_val = curr_filter["val"]
        values = priorities[:priorities.index(curr_val) + 1]
        curr_filter["list"] = True
        curr_filter["val"] = values
    elif token.lower_ in statuses:
        # The user specified the exact status.
        curr_filter["name"] = "status"
        if "op" not in curr_filter:
            curr_filter["op"] = "="
        curr_filter["val"] = token.orth_
    elif token.lower_ in mes:
        curr_filter["val"] = user
    elif ("name" in curr_filter and "op" in curr_filter and
            "val" not in curr_filter):
        # We already have the other two,
        # this is likely the value.
        # XXX Risky assumption.
        curr_filter["val"] = token.orth_
    else:
        processed = False

    if token.orth_ in translate_status_tokens:
        curr_filter["status_tokens"].add(translate_status_tokens[token.orth_])

    if processed:
        already_processed.append(token)

    # Go through the semantic tree and figure out the
    # rest of the filter values.
    for child in token.children:
        get_filter(child, texts, user, already_processed, curr_filter)
    return curr_filter


def natural_to_query(query, user):
    trac_query = []
    logger.info("Processing query: %r", query)

    # Replace quoted string with unique ids, as the
    # user clearly wants us to interpret them as
    # single tokens
    texts = {}
    inc = 1
    while True:
        repl = str(inc) + UNIQUE_M
        try:
            text = match_re.search(query).group()
        except AttributeError:
            break
        texts[repl] = text.strip('"\'')
        query = match_re.sub(repl, query, 1)
        inc += 1

    logger.debug("Found text search: %s", texts)
    logger.debug("Query: %s", query)
    # Replace component names with unique ids
    # as those are known to us already
    for i, j in components.items():
        query = query.replace(i, j)
    logger.debug("Replaced components %r", query)

    # If we process and accept a token as part
    # of a filter while going through the
    # semantic tree, store it here, so we don't
    # wrongly reuse it in another filter.
    already_processed = []
    tokens = nlp(query.decode("utf8"))
    for token in tokens:
        logger.debug("Checking token: %s", token)
        if token in already_processed:
            logger.debug("Already processed: %s", token)
            continue

        processed = False

        # Try to extract a filter by going trough the
        # semantic tree, starting from this token, while
        # ignoring any already processed tokens.
        new_already_processed = list(already_processed)
        f = get_filter(token, texts, user, new_already_processed)
        logger.debug("Resulting filter: %s", f)

        try:
            if f["not"]:
                f["op"] = f["op"].replace("=", "=!")
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
                status = tokenized_statuses[frozenset(f["status_tokens"])]
                if f["not"]:
                    trac_query.append("status!=" + status)
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


