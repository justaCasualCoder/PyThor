from pythor import PyThor
import readline
import platform
import sys
from usb.core import USBTimeoutError, USBError
from alive_progress import alive_bar
if platform.system() == 'Linux':
    import readline
class Shell:
    def __init__(self):
        self.tool = PyThor()
        self.commands = {
            "help": [self.print_help, "Print this help"],
            "exit": [sys.exit(), "Exit the program"],
            "connect": [self.tool.connect, "Connect to device"],
            "begin": [self.tool.begin_session, ("resume",), "Begin session"],
            "flashFile": [
                self.flash_file,
                ("file", "partition"),
                "Flash a raw image file",
            ],
            "reboot": [self.tool.reboot, "Reboot device"],
            "shutdown": [self.tool.shutdown, "Shutdown device"],
            "wipe": [self.tool.factory_reset, "Factory reset device"],
            "printPit": [self.tool.print_pit, "Print PIT"],
            "clear": [lambda: print("\033[H\033[J", end=""), "Clear Console"],
        }

    def execute_cmd(self, cmd, args):
        try:
            if cmd == "begin":
                if "resume" in args:
                    self.tool.begin_session(resume=True)
                    return
            self.commands[cmd][0](*args)
        # Catch errors, to prevent the program from exiting abruptly
        except ValueError as e:
            print(f"\033[91mError: {e}\033[0m")
        except USBTimeoutError as e:
            print(f"\033[91m{e}. Has a session already been started?\033[0m")
        except TypeError as e:
            print(f"\033[91m{e}\033[0m")
        except USBError as e:
            print(f"\033[91m{e}. Are you already connected?\033[0m")

    def print_help(self):
        help_str = f"\033[92mCommands available to use: {len(self.commands)}\033[0m\n"
        for command, h in self.commands.items():
            if type(h[1]) == str:
                help_str += f"\033[93m{command} - {h[1]}\033[0m\n"
            else:
                help_str += f"\033[93m{command} {' '.join(f'[{arg}]' for arg in h[1])} - {h[2]}\033[0m\n"
        print(help_str.strip())

    def flash_file(self, file, partition):
        cm = alive_bar(total=100, manual=True, calibrate=40)
        bar = cm.__enter__()

        def progress_callback(p):
            bar(p / 100)

        self.tool.flash_file(file, partition, progress_callback)
        cm.__exit__(None, None, None)

    def run(self):
        while True:
            try:
                cmd = input(">> ")
            except KeyboardInterrupt:
                print("\033[2K\033[1GUse \033[1m\x1B[3m\033[93mexit\x1B[0m next time.")
                sys.exit()
            cmd, *args = cmd.strip().split(" ")
            if cmd and cmd not in self.commands:
                print("\033[93mCommand not found\033[0m")
            elif cmd:
                self.execute_cmd(cmd, args)


if __name__ == "__main__":
    pythor_shell = Shell()
    pythor_shell.run()
