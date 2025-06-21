class GreenAPIError(RuntimeError):
    pass


class GreenAPIThrottleError(GreenAPIError):
    """429"""
    pass
