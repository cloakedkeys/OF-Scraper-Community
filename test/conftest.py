import atexit
import os
import shutil
import sys
import tempfile

# ofscraper parses sys.argv when settings are first read, so hide pytest's
# own command-line flags from it.
sys.argv = sys.argv[:1]

# Give every test session (one per xdist worker) its own empty home folder,
# set before ofscraper is imported, so no test can read or write the real
# ~/.config/ofscraper and every run starts like a fresh install. The app finds
# its config folder with Path.home() (HOME, or USERPROFILE on Windows) plus
# OFSC_CONFIG_DIR, so a user's own OFSC_CONFIG_DIR is dropped too.
_scratch_home = tempfile.mkdtemp(
    prefix=f"ofs-test-home-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-"
)
os.environ["HOME"] = _scratch_home
os.environ["USERPROFILE"] = _scratch_home
os.environ.pop("OFSC_CONFIG_DIR", None)
atexit.register(shutil.rmtree, _scratch_home, ignore_errors=True)

# The app's entry module imports ofscraper's packages in an order that avoids
# their circular imports; importing a leaf module first fails.
import ofscraper.main.open.load  # noqa: E402,F401

import pytest  # noqa: E402

from ofscraper.utils.args.parse.commands.main import program  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_subcommands():
    # parse_args registers the subcommands on the shared program, and cloup
    # refuses to register one twice. A test that parses for real leaves them
    # registered, so a later test whose first settings read parses again fails
    # with 'command "manual" already exists' (#727). Put the program back.
    commands = dict(program.commands)
    section = dict(program._default_section.commands)
    yield
    program.commands.clear()
    program.commands.update(commands)
    program._default_section.commands.clear()
    program._default_section.commands.update(section)
