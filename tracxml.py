"""Implements a transport for Trac's xmlrpclib that is compatible with the
AccountManager plug-in and uses requests for Session handling.
"""

from __future__ import print_function

try:
    from xmlrpc import client
except ImportError:
    import xmlrpclib as client

import requests


class RequestsTransport(client.Transport):
    proto = "http"
    # This might work, but haven't tested
    accept_gzip_encoding = False
    _content_type = "text/xml"

    def __init__(self, use_datetime=0):
        client.Transport.__init__(self, use_datetime=use_datetime)
        self.session = requests.Session()

    def auth_trac(self, host, auth_details):
        if self.is_auth():
            return
        # Do a get first so the trac_form_token cookie is set in the session
        self.session.get(self.get_url(host, "/login"))
        form_token = self.session.cookies["trac_form_token"].split(";")[0]

        user, password = auth_details.split(":", 1)
        self.session.post(self.get_url(host, "/login"),
                          data={"user": user, "password": password,
                                "__FORM_TOKEN": form_token})

    def is_auth(self):
        return "trac_auth" in self.session.cookies

    def get_url(self, host, handler):
        return "%s://%s%s" % (self.proto, host, handler)

    def single_request(self, host, handler, request_body, verbose=0):
        if "@" in host:
            auth_details, host = host.rsplit("@", 1)
            self.auth_trac(host, auth_details)

        headers = {"User-Agent": self.user_agent,
                   "Content-Type": self._content_type}
        if self._extra_headers:
            headers.update(dict(self._extra_headers))

        response = self.session.get(self.get_url(host, handler),
                                    data=request_body, headers=headers)

        if response.ok:
            self.verbose = verbose
            return self.parse_response(response)

        response.close()

        raise client.ProtocolError(host + handler, response.status_codes,
                                   response.reason, "")

    def parse_response(self, response):
        p, u = self.getparser()

        for line in response.iter_lines():
            if self.verbose:
                print("body:", repr(line))
            p.feed(line)

        response.close()
        p.close()

        return u.close()


class SafeRequestsTransport(RequestsTransport):
    proto = "https"


try:
    import jsonrpclib.jsonrpc
except ImportError:
    pass
else:
    class JSONRequestsTransport(RequestsTransport):
        """Extends the XMLRPC transport where necessary."""
        _connection = (None, None)
        _extra_headers = []
        _content_type = "application/json"

        def send_content(self, connection, request_body):
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(request_body)))
            connection.endheaders()
            if request_body:
                connection.send(request_body)

        def getparser(self):
            target = jsonrpclib.jsonrpc.JSONTarget()
            return jsonrpclib.jsonrpc.JSONParser(target), target

    class SafeJSONRequestsTransport(JSONRequestsTransport):
        proto = "https"
