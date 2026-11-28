from ofscraper.utils.markup import escape_text


def authwarning(authFile):
    # imported here: utils.console can't be the first ofscraper module loaded
    # (its settings -> config.file import cycle), and this module can be
    import ofscraper.utils.console as console_

    # auth messages show even while a screen has quieted the shared console
    console = console_.get_other_console()
    console.print(
        "[bold yellow]For an example of how your auth file should look see \
            \n [bold deep_sky_blue2]https://cloakedkeys.gitbook.io/ofsc/setup/getting-started/auth#example[/bold deep_sky_blue2][/bold yellow]"
    )
    console.print(
        f"[bold yellow]If you still can't authenticate after editing from script consider manually edit the file at\n[bold deep_sky_blue2]{escape_text(authFile)}[/bold deep_sky_blue2][/bold yellow]"
    )
