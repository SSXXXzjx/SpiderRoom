"""Load the bundled pinterest-dl project before any globally installed copy."""
import sys
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent / 'vendor' / 'pinterest-dl'
sys.path.insert(0, str(VENDOR_ROOT))

from pinterest_dl import PinterestDL
from pinterest_dl.api.api import Api
from pinterest_dl.api.pinterest_response import PinResponse
from pinterest_dl.download.request_builder import build_get
