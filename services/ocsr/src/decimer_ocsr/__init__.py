"""DECIMER Image Transformer (OCSR) model service."""

import os

__version__ = "0.1.0"

# TensorFlow reads this when it is imported, which `OcsrModel.load()` does later, so it has to be
# set before that: importing this package is enough. 2 hides the INFO and WARNING lines (oneDNN,
# CPU features, TensorRT) and keeps ERROR, so a real failure in the C++ layer is still logged. A
# value set by whoever runs the service wins. (`DECIMER` assigns "3" itself, but only after it has
# imported TensorFlow; the three cuDNN/cuFFT/cuBLAS ERROR lines at startup still appear.)
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
