"""The arq worker. See the "Worker" section of the API README.

This package is deliberately empty of imports: the render child process imports
`decimer_api.worker.render` and must not drag in RDKit, httpx or arq.
"""
