# -*- coding: utf-8 -*-
"""Convert Trac's WikiFormat text to Markdown."""

import re


FLAVOURS = {
    "markdown": {
        "bold": r"**\1**",
        "italic": r"*\1*",
        "ul": r"*\2",
        "ol": r"\1.\2",
        "link": r"[[\2|\1]]",
        "wiki_link": r"[%s/wiki/\2](\1)",
        "ticket_link": r"[%s/ticket/\2](\1)",
        "changeset_link": r"[%s/changeset/\2](\1)",
        "auto_ticket": r"\1[%s/ticket/\2](#\2)",
        "auto_changeset": r"\1[%s/changeset/\2](\[\2\])",
    },
    # mrkdwn is Slack's markup language, which is similar to Markdown.
    "mrkdwn": {
        "bold": r"*\1*",
        "italic": r"_\1_",
        "ul": u"\\1•\\2",
        "ol": r"\1\2",
        "link": r"<\1|\2>",
        "wiki_link": r"<%s/wiki/\1|\2>",
        "ticket_link": r"<%s/ticket/\1|\2>",
        "changeset_link": r"<%s/changeset/\1|\2>",
        "auto_ticket": r"\1<%s/ticket/\2|#\2>",
        "auto_changeset": r"\1<%s/changeset/\2|\[\2\]>",
    },
}


def convert(text, base="", flavour="markdown"):
    """Convert the passed text from WikiFormatting to Markdown."""
    transforms = FLAVOURS[flavour]

    # Convert code blocks.
    # XXX This doesn't handle nested blocks, but those should be
    # XXX uncommon. We would probably need to properly parse the text
    # XXX to handle that.
    text = re.sub(r"^{{{\s(.*?)\s}}}$", r"```\n\1\n```", text,
                  flags=re.DOTALL | re.MULTILINE)
    # Also inline code:
    text = re.sub(r"\{\{\{([^\n]+?)\}\}\}", r"`\1`", text)

    # Convert bold and italic text.
    # Note that it's important to do bold before italic, to avoid having
    # to distinguish them.
    text = re.sub(r"'''(.+)'''", transforms["bold"], text)
    text = re.sub(r"''(.+)''", transforms["italic"], text)
    # Backticks ("monospaced" in Trac terms) and quotes
    # ("Discussion citations") are the same so do not need conversion.

    # TODO: Definition lists.
    # TODO: Blockquotes (maybe these are identical?).
    # TODO: Tables (would need MD extension).
    # TODO: Images.
    # TODO: Line breaks.
    # TODO: Macros (don't know what could be done with these, though).

    # Convert headings.
    # Again, note that the order is important to keep this simple.
    # Slack will not handle these specially, but they look nicer in
    # markdown, so keep the same conversion.
    text = re.sub(r"====\s+(.+?)\s+====", r"#### \1", text)
    text = re.sub(r"===\s+(.+?)\s+===", r"### \1", text)
    text = re.sub(r"==\s+(.+?)\s+==", r"## \1", text)
    text = re.sub(r"=\s+(.+?)\s+=", r"# \1", text)

    # Convert lists.
    text = re.sub(r"(^\s*)\*(\s)", transforms["ul"], text, flags=re.MULTILINE)
    text = re.sub(r"(^\s*)\-(\s)", transforms["ul"], text, flags=re.MULTILINE)
    text = re.sub(r"^(\s*\d+)\.(\s)", transforms["ol"], text,
                  flags=re.MULTILINE)

    # Convert links.
    text = re.sub(r"\[(?:wiki:)([^\s]+)\s(.+)\]",
                  transforms["wiki_link"] % base, text)
    text = re.sub(r"\[(?:ticket:)([^\s]+)\s(.+)\]",
                  transforms["ticket_link"] % base, text)
    text = re.sub(r"\[(?:changeset:)([^\s]+)\s(.+)\]",
                  transforms["changeset_link"] % base, text)
    text = re.sub(r"\[([^\s]+)\s(.+)\]", transforms["link"], text)
    # TODO: automatic CamelCase links (which are terrible anyway).
    # These links show the URL so do not need custom transforms.
    text = re.sub(r"(\s)wiki:([A-Za-z0-9]+)(\s)",
                  r"\1%s/wiki/\2\3" % base, text)
    text = re.sub(r"(\s)ticket:([0-9]+)(\s)",
                  r"\1%s/ticket/\2\3" % base, text)
    # The automatic linking won't work if the content is the very first
    # text, but I think this is good enough.
    # Automatic links of #1234 to tickets.
    text = re.sub(r"(\s)#(\d+)\b", transforms["auto_ticket"] % base, text)
    # r1234 and [124] to changesets.
    # XXX Maybe git changeset markers have more than 0-9? Hex?
    text = re.sub(r"(\s)r(\d+)\b", transforms["auto_changeset"] % base, text)
    text = re.sub(r"(\s)\[(\d+)\]\b", transforms["auto_changeset"] % base,
                  text)
    text = re.sub(r"(\s)changeset:([0-9]+)(\s)",
                  r"\1%s/changeset/\2\3" % base, text)
    return text
