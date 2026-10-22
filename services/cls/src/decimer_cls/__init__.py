"""DECIMER Image Classifier model service."""

import os

__version__ = "0.1.0"

# TensorFlow reads this when it is imported, which `ClsModel.load()` does later, so it has to be set
# before that: importing this package is enough. 2 hides the INFO and WARNING lines (oneDNN, CPU
# features, TensorRT) and keeps ERROR, so a real failure in the C++ layer is still logged. A value
# set by whoever runs the service wins.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
