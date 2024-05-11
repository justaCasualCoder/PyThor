from .pythor import PyThor


def cli():
    """Entry point for application script"""
    from .pythor_cli import Shell

    shell = Shell()
    shell.run()
