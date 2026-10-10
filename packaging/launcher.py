"""Entry point of the packaged app.

Libraries start helper processes (multiprocessing's resource tracker) by re-running this
executable. freeze_support() must run before anything else is imported: it turns those
re-runs into the helper and exits, instead of each one importing the app and starting
another helper.
"""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    import app
    app.main()
