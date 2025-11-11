class InvalidHash(Exception):
    """Raised when a hash is invalid."""
    message = 'Invalid hash!'


class FileNotFound(Exception):
    """Raised when a file is not found."""
    message = 'File not found!'