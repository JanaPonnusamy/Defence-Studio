"""Run core tests with Python network sockets denied."""
import socket, unittest
from unittest.mock import patch

def denied(*args,**kwargs): raise AssertionError("Network use prohibited in core tests")
with patch.object(socket,"socket",side_effect=denied),patch.object(socket,"create_connection",side_effect=denied):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests"))
raise SystemExit(0 if result.wasSuccessful() else 1)
