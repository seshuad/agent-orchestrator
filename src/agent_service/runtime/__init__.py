"""The programs a run worker ships. Every `command:` the compiler emits points at one of them.

In the service, the gateway is a separate process holding the credential vault, and the shim
only forwards calls to it. In this prototype the shim and the gateway are the same process,
and connections serve sample data from AGENT_SERVICE_SAMPLE_DATA instead of real accounts.
"""
