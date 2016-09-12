import os

try:
    import configparser
except ImportError:
    import ConfigParser as configparser


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
            "statuses": "",
            "resolutions": "fixed,invalid,wontfix,duplicate,worksforme,cantfix",
            "describe_fields": "type,component,priority,status,milestone",
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
    conf = configparser.RawConfigParser()
    # Load in default values.
    for section, values in defaults.items():
        conf.add_section(section)
        for option, value in values.items():
            conf.set(section, option, value)

    if os.path.exists("/etc/trac-slack.conf"):
        conf.read("/etc/trac-slack.conf")
    return conf
