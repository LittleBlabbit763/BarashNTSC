import os, sys
if getattr(sys, "frozen", False):
    paths = [os.path.dirname(sys.executable), sys._MEIPASS]
    os.environ["PATH"] = os.pathsep.join(paths) + os.pathsep + os.environ.get("PATH", "")
