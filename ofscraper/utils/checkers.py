import logging

import ofscraper.data.api.init as init
import ofscraper.sites as sites
import ofscraper.utils.auth.file as auth_file
import ofscraper.utils.auth.make as make

log = logging.getLogger("shared")


def check_auth():
    status = None
    log.info("checking auth status")
    if sites.is_fansly():
        import ofscraper.sites.fansly.auth as fansly_auth

        fansly_auth.load()
    while status != "UP":
        status = init.getstatus()
        if status != "UP":
            log.info("Auth Failed")
            if sites.is_fansly():
                fansly_auth.make_auth()
            else:
                make.make_auth(auth=auth_file.read_auth())
        else:
            break
