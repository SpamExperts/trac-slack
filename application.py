#! /usr/bin/env python
# -*- coding: utf-8 -*-

""""""

import os
import pwd
import json
import logging
import calendar
import functools

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

try:
    from xmlrpc import client
except ImportError:
    import xmlrpclib as client

try:
    import raven
    import raven.transport
    from raven.contrib.flask import Sentry
    from raven.handlers.logging import SentryHandler

    _has_raven = True
except ImportError:
    _has_raven = False

import flask
import flask.views
from flask import jsonify
from mimerender import FlaskMimeRender

import tracxml
import natural
import trac_to_markdown

from core import load_configuration

CONF = load_configuration()
# This is the WSGI application that we are creating.
application = flask.Flask(__name__)
mimerender = FlaskMimeRender()(default='json', json=jsonify)


def setup_logging(logger):
    user = CONF.get("logging", "user")
    filename = CONF.get("logging", "file")
    sentry = CONF.get("logging", "sentry")
    level = getattr(logging, CONF.get("logging", "level").upper())
    if user and pwd.getpwuid(os.getuid()).pw_name != user:
        return

    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    logger.setLevel(logging.DEBUG)

    if filename:
        file_handler = logging.FileHandler(filename)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

    if sentry and _has_raven:
        client = raven.Client(sentry,
                              enable_breadcrumbs=False,
                              transport=raven.transport.HTTPTransport)
        # Wrap the application in Sentry middleware.
        Sentry(application, client=client, logging=True,
               level=logging.WARN)
        # Add Sentry handle to application logger.
        sentry_handler = SentryHandler(client)
        sentry_handler.setLevel(logging.WARNING)
        logger.addHandler(sentry_handler)

        null_loggers = [
            logging.getLogger("sentry.errors"),
            logging.getLogger("sentry.errors.uncaught")
        ]
        for null_logger in null_loggers:
            null_logger.handlers = [logging.NullHandler()]


setup_logging(application.logger)
setup_logging(natural.logger)


def verify_token():
    token = flask.request.form["token"]
    conf_token = CONF.get("slack", "token")
    if token != conf_token:
        return "Invalid token"


application.before_request(verify_token)

try:
    import jsonrpclib.config
    import jsonrpclib.jsonrpc
except ImportError:
    trac_proxy = client.ServerProxy(
        "https://%s:%s@%s/login/rpc" %
        (CONF.get("trac", "user"), CONF.get("trac", "password"),
         CONF.get("trac", "host")), transport=tracxml.SafeRequestsTransport()
    )
else:
    jsonrpclib.config.use_jsonclass = False
    trac_proxy = jsonrpclib.jsonrpc.ServerProxy(
        "https://%s:%s@%s/login/rpc" %
        (CONF.get("trac", "user"), CONF.get("trac", "password"),
         CONF.get("trac", "host")),
        transport=tracxml.SafeJSONRequestsTransport()
    )

INTRO = (u"Trac slash command allows you to query Trac tickets from slack. The "
         u"Trac slash command will do its best to interpret your query from "
         u"english, and translate to a Trac query. Some examples include:")

BODY_TEXT = u"""
 • `/trac 12354` - to view detailed specifications of a ticket
 • `/trac query status=new&type=task&priority=normal` - to view a list of tickets matching this query
 • `/trac show my tickets` - to list all tickets assigned to you
 • `/trac show my normal or higher bug tickets`
 • `/trac list task tickets opened in the last three weeks`
 • `/trac list tickets where I'm in the cc`
 • `/trac list feature tickets that need testing modified in the last week`
 • `/trac text like "Some error I know", opened two months ago`
"""

HELP_TEXT = INTRO + BODY_TEXT

QUERY_TEMPLATE = (u" • <https://%(host)s/ticket/%(number)s|#%(number)s> "
                  u"- %(summary)s")


class QueryTrac(flask.views.MethodView):
    _to_md = functools.partial(trac_to_markdown.convert,
                               base="https://%s" % CONF.get("trac", "host"),
                               flavour="mrkdwn")

    def _escape(self, value):
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">",
                                                                        "&gt;")

    def _get_tick_attributes(self, ticket):
        escape = self._escape
        to_md = self._to_md
        attributes = dict(trac_proxy.ticket.get(ticket)[3])
        stamp = calendar.timegm(attributes['time'].timetuple())
        attributes["stamp"] = stamp
        attributes["host"] = CONF.get("trac", "host")
        attributes["number"] = str(ticket)
        attributes["summary"] = escape(attributes["summary"])
        attributes["description"] = to_md(escape(attributes["description"]))
        attributes["keywords"] = escape(attributes["keywords"])
        if attributes["status"] == "closed":
            attributes["status+"] = "%s: %s" % (attributes["status"],
                                                attributes["resolution"])
        else:
            attributes["status+"] = attributes["status"]
        return attributes

    def _handle_query(self, query):
        limit = int(CONF.get("trac", "limit"))
        result = []
        try:
            tickets = trac_proxy.ticket.query(query)
        except Exception:
            return {"text": ("Oops, something went wrong! :sweat:\n"
                             "The query might not be valid?")}
        total_tickets = len(tickets)
        for ticket in tickets[:limit]:
            attr = self._get_tick_attributes(ticket)
            result.append(QUERY_TEMPLATE % attr)
        if total_tickets > limit:
            result.append("")
            result.append("_%s tickets not shown!_" % (total_tickets - limit))
            result.append("_The rest of the results available "
                          "<https://%s/query?%s|here>_" %
                          (CONF.get("trac", "host"), query))
        elif not total_tickets:
            result.append("No tickets found")
            result.append("_See in <https://%s/query?%s|trac>_" %
                          (CONF.get("trac", "host"), query))
        else:
            result.append("_See in <https://%s/query?%s|trac>_" %
                          (CONF.get("trac", "host"), query))
        return {"text": "\n".join(result), "response_type": "in_channel"}

    def _handle_describe(self, query):
        try:
            ticket = trac_proxy.ticket.query(query)[0]
        except IndexError:
            # This should be ephemeral
            return {"text": "No such ticket"}
        attr = self._get_tick_attributes(ticket)
        color = "#f5f5ef"
        if attr["type"] == "feature":
            color = "good"
        elif attr["type"] == "task":
            color = "warning"
        elif attr["type"] == "bug":
            color = "danger"
        fields = [
            {
                "title": field.title(),
                "value": attr.get(field, "_(unknown)_"),
                "short": True,
            }
            for field in CONF.get("trac", "describe_fields").split(",")
            if attr[field]
            ]
        return {
            "response_type": "in_channel",
            "attachments": [
                {
                    "fallback": attr["summary"],
                    "color": color,
                    "title": attr["summary"],
                    "author_name": attr["owner"],
                    "title_link": "https://%(host)s/ticket/%(number)s" % attr,
                    "text": self._to_md(attr["description"]),
                    "fields": fields,
                    "footer": "#%(number)s" % attr,
                    "ts": attr["stamp"],
                    "mrkdwn_in": ["text"],
                }
            ]
        }

    def handle_help(self):
        return {"text": HELP_TEXT}

    def handle_adjust(self, user, query):
        possible_fields = CONF.get("trac", "adjust_fields")
        if not possible_fields:
            return {"text": "Sorry, I'm not set up for this yet."}
        possible_fields = possible_fields.split(",")
        try:
            ticket_id, field, value, details = query.split(None, 3)
        except ValueError:
            return {"text": "Sorry, I didn't understand that."}
        if field not in possible_fields:
            if len(possible_fields) == 1:
                possible = possible_fields[0]
            elif len(possible_fields) == 2:
                possible = " or ".join(possible_fields)
            else:
                possible = ", ".join(possible_fields[:-1])
                possible = "%s, or %s" % (possible, possible_fields[-1])
            return {"text":
                    "Sorry, I don't know how to do that yet. Try %s." %
                    possible}
        try:
            value = float(value)
        except ValueError:
            # We probably could figure out an appropriate way to handle
            # non-numeric fields.
            return {"text": "Sorry, I can only increase numeric fields."}
        try:
            ticket_id = int(ticket_id.lstrip("#"))
        except ValueError:
            example_details = CONF.get("trac", "example_adjust_details")
            return {
                "text": "Sorry, I didn't understand that. I'm expecting "
                "`adjust [ticket id] [field] [value] [details]`, like "
                "`adjust #12345 %s 5 %s`" %
                (possible_fields[0], example_details)}
        try:
            template = CONF.get("trac", "adjust_template_%s" % field)
        except configparser.NoOptionError:
            pass
        else:
            details = template % {"details": details, "value": value}
        attributes = dict(trac_proxy.ticket.get(ticket)[3])
        if attributes[field]:
            try:
                new_value = float(attributes[field]) + value
            except ValueError:
                return {"text": "Sorry, %s's %s is not a number." %
                        (ticket_id, field)}
        else:
            new_value = value
        changes = {
            "description": "%s\n%s" % (attributes["description"], details),
            field: str(new_value),
        }
        trac_proxy.ticket.update(ticket_id, "", changes, True, user)
        return {"text": "Done! New %s for #%s is %s" %
                (field, ticket_id, new_value)}

    @mimerender
    def post(self):
        text = flask.request.form["text"]
        user = flask.request.form["user_name"]
        if text.lower() == "help":
            return self.handle_help()
        try:
            command, query = text.split(None, 1)
            assert command.lower() in ("describe", "show", "query", "adjust")
        except (ValueError, AssertionError):
            # Try to figure out what the user wants
            try:
                command, query = "describe", int(text.lstrip('#'))
            except (ValueError, TypeError):
                query = text
                if "=" in text or "&" in text:
                    command = "query"
                else:
                    command = "show"

        command = command.lower()
        if command == "describe":
            return self._handle_describe("id=%s" % query)

        if command == "adjust":
            return self.handle_adjust(user, query)

        if command == "show":
            query = natural.natural_to_query(query, user)
            if not query:
                # Might be nice to have random responses.
                return {
                    "text": ("Didn't quite get that :thinking_face: \n"
                             "Have you tried quoting your text searches?")}
            return self._handle_query(query)

        if command
            return self._handle_query(query)

        return {"text": "Invalid command: %s" % command}


application.add_url_rule(
    CONF.get("slack", "endpoint"),
    view_func=QueryTrac.as_view('trac_query')
)


@application.route(CONF.get("slack", "action-endpoint"), methods=['POST'])
def slack_action():
    """Route the action to the appropriate method."""
    data = json.loads(flask.request.form["payload"])
    user = data["user"]["name"]
    callback_id = data["callback_id"]
    if callback_id.startswith("adjust_"):
        field, tickets = callback_id.split("_")[1:]
        tickets = tickets.split(",")
        # We only support one action.
        action = data["actions"][0]
        # We only support one option.
        option = action["selected_options"][0]["value"]
        for ticket in tickets:
            changes = {field: option}
            trac_proxy.ticket.update(int(ticket), "", changes, True, user)
        return json.dumps({"text": "Done!"})
    return json.dumps({"text": "Unknown action."})


@application.route(CONF.get("slack", "options-endpoint"), methods=['POST'])
def slack_options():
    """Provide options when users invoke message menus."""


# Testing code.
if __name__ == "__main__":
    application.run(debug=True)
