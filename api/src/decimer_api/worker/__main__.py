"""`python -m decimer_api.worker`: run the arq worker."""

from .arq_app import main

if __name__ == "__main__":
    main()
