"""Human-facing numbers for purchase and warehouse requests.

The database id stays internal (it is what URLs and relations use).  The
number shown to people is that id shifted so the very first request is
100000, the second 100001, and so on.  It is returned as a plain string of
Latin digits so Django's locale handling never adds separators or converts
the digits.
"""

REQUEST_NUMBER_START = 100000


def request_number(value):
    """Return the displayed request number for a model instance or a raw pk.

    Returns an empty string when there is nothing to number (unsaved object,
    empty value).
    """
    if value is None:
        return ''
    pk = getattr(value, 'pk', value)
    if pk is None or pk == '':
        return ''
    try:
        return str(REQUEST_NUMBER_START + int(pk) - 1)
    except (TypeError, ValueError):
        return ''
