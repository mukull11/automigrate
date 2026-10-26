from lib import connect


def make_connection(host="db"):
    return connect(host, mode="strict")
