"""Logging helper shared by the spike scripts."""

import logging


def get_logger(logger_name: str) -> logging.Logger:
    """Return a logger that writes plain messages to stderr.

    Args:
        logger_name: Name of the logger, usually the module name.

    Returns:
        A configured ``logging.Logger``.
    """
    logger = logging.getLogger(logger_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
