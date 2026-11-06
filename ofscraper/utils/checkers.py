import logging

import ofscraper.data.api.init as init
import ofscraper.utils.auth.file as auth_file
import ofscraper.utils.auth.make as make

log = logging.getLogger("shared")


def check_auth():
    status = None
    log.info("checking auth status")
    while status != "UP":
        status = init.getstatus()
        if status == "NETWORK":
            # the auth may be fine, so don't ask for it again
            raise SystemExit(1)
        if status != "UP":
            log.info("Auth Failed")
            make.make_auth(auth=auth_file.read_auth())
        else:
            break
