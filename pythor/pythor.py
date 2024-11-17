import os.path
import usb
import struct
from treelib import Tree
import math
from io import BytesIO
from usb.core import USBTimeoutError
import logging

logging.basicConfig(level=logging.WARNING)


class PyThor:
    def __init__(self):
        self.dev = None
        self.session_started = False
        self.t_flash_enabled = False
        self.flashpacketsize = int
        self.sequencesize = int
        self.partitions = {}

    def connect(self):
        """Connects to Samsung Device"""
        self.dev = usb.core.find(idVendor=0x04E8)
        if self.dev is None:
            raise ValueError("Could not find a Samsung device")
        self.dev.set_configuration()

    def pack(self, data, offset, buf):
        """
        Packs data into buffer

        - `data`: The data to add to buffer
        - `offset`: Offset of the data in the buffer
        - `buf`: Buffer to add data to.
        """
        struct.pack_into("i", buf, offset, data)

    def write(self, data):
        """
        Writes data to device

        - `data`: Data to send to device
        """
        if self.dev and self.session_started:
            self.dev.write(0x1, data)
        else:
            raise ValueError("You need to start a session first")

    def read(self, timeout: int = None):
        """
        Read data from device

        - `timeout` (optional): read timeout in ms.
        """
        if self.dev and self.session_started:
            ret = self.dev.read(0x81, 0x1000, timeout)
            return ret
        else:
            raise ValueError("You need to start a session first")

    def print_pit(self):
        """Print PIT of device"""
        tree = Tree()
        root = tree.create_node("Partitions")
        if not self.partitions:
            self.get_pit()
        for partition, attributes in self.partitions.items():
            node = tree.create_node(partition, parent=root)
            for key, value in attributes.items():
                tree.create_node(f"{key}: {value}", parent=node)
        print(tree.show(stdout=False))

    def get_pit(self):
        """
        Read PIT from device

        Returns:
            pit_data (bytearray): PIT Data
        """
        # Request PIT dump
        buf = bytearray(1024)
        self.pack(0x65, 0, buf)
        self.pack(0x01, 4, buf)
        self.write(buf)
        ret = self.read()
        size = struct.unpack_from("<I", ret, 4)[0]
        blocks = math.ceil(size / 500)
        logging.debug(f"PIT size is {size}, {blocks} total blocks")
        pit_buf = bytearray(size)
        for i in range(blocks):
            buf = bytearray(1024)
            self.pack(0x65, 0, buf)
            self.pack(0x02, 4, buf)
            self.pack(i, 8, buf)
            self.write(buf)
            ret = self.read()
            pit_buf[i * 500 : i * 500 + len(ret)] = ret
        # Send PIT finish
        buf = bytearray(1024)
        self.pack(0x65, 0, buf)
        self.pack(0x03, 4, buf)
        # Read ZLP
        try:
            self.read()
        except usb.USBError:
            # This is fine.
            pass
        self.write(buf)
        self.read()
        self.parse_pit(pit_buf)
        return pit_buf

    def parse_pit(self, pit: bytearray):
        """
        Parse PIT

        Arguments:

        - `pit`: Pit data.
        """
        with BytesIO(pit) as reader:
            magic_number = struct.unpack("<I", reader.read(4))[0]
            if magic_number != 0x12349876:
                raise ValueError("Magic Number Mismatch")
            entries = struct.unpack("<I", reader.read(4))[0]
            unknown = reader.read(8).decode("utf-8")
            project = reader.read(8).decode("utf-8")
            reserved = struct.unpack("<i", reader.read(4))[0]
            for i in range(entries):
                entry = {
                    "BinaryType": struct.unpack("i", reader.read(4))[0],
                    "DeviceType": struct.unpack("i", reader.read(4))[0],
                    "PartitionID": struct.unpack("i", reader.read(4))[0],
                    "Attributes": struct.unpack("i", reader.read(4))[0],
                    "UpdateAttributes": struct.unpack("i", reader.read(4))[0],
                    "BlockSize": struct.unpack("i", reader.read(4))[0],
                    "BlockCount": struct.unpack("i", reader.read(4))[0],
                    "FileOffset": struct.unpack("i", reader.read(4))[0],
                    "FileSize": struct.unpack("i", reader.read(4))[0],
                    "Partition": reader.read(32)
                    .decode("utf-8")
                    .strip()
                    .strip("\x20")
                    .strip("\x00"),
                    "FileName": reader.read(32)
                    .decode("utf-8")
                    .strip()
                    .strip("\x20")
                    .strip("\x00"),
                    "DeltaName": reader.read(32)
                    .decode("utf-8")
                    .strip()
                    .strip("\x20")
                    .strip("\x00"),
                }
                self.partitions[entry["Partition"]] = entry

    def flash(
        self,
        stream: BytesIO,
        entry: str,
        progress_callback,
        update_bootloader=False,
        efs_clear=False,
    ):
        """
        Flash a file to device.

        Arguments:

        - `stream`: An open file stream
        - `entry`: Partition name
        - `progress_callback`: Callback for progress.
        - `update_bootloader` (optional)
        - `efs_clear` (optional)
        """

        def get_size(s):
            s.seek(0, 2)
            size = s.tell()
            s.seek(0)
            return size

        if not self.partitions:
            self.get_pit()
        entry = self.partitions[entry]
        length = get_size(stream)
        self.send_total_bytes(length)
        buf = bytearray(1024)
        self.pack(0x66, 0, buf)
        self.pack(0x00, 4, buf)
        self.write(buf)
        self.read()
        sequence = self.flashpacketsize * self.sequencesize
        sequences = length // sequence
        last_sequence = length % sequence
        if last_sequence != 0:
            sequences += 1
        else:
            last_sequence = sequence
        for i in range(sequences):
            if i + 1 == sequences:
                last = True
            real_size = last_sequence if last else sequence
            aligned_size = real_size
            if real_size % self.flashpacketsize != 0:
                aligned_size += self.flashpacketsize - real_size % self.flashpacketsize
            buf = bytearray(1024)
            self.pack(0x66, 0, buf)
            self.pack(0x02, 4, buf)
            self.pack(aligned_size, 8, buf)
            self.write(buf)
            self.read()
            parts = aligned_size // self.flashpacketsize
            for j in range(parts):
                buf = bytearray(self.flashpacketsize)
                stream.readinto(buf)
                self.write(buf)
                ret = self.read()
                index = ret[4]
                if index != j:
                    logging.warning("Bootloader index is wrong!")
                progress_callback((j / parts) * 100)
            progress_callback(100)
            if entry["BinaryType"] == 1:
                logging.debug("Flashing Modem")
                buf = bytearray(1024)
                self.pack(0x66, 0, buf)
                self.pack(0x03, 4, buf)
                self.pack(0x01, 8, buf)
                self.pack(real_size, 12, buf)
                self.pack(entry["BinaryType"], 16, buf)
                self.pack(entry["DeviceType"], 20, buf)
                self.pack(1 if last else 0, 24, buf)
                self.write(buf)
            else:
                logging.debug("Flashing firmware")
                buf = bytearray(1024)
                self.pack(0x66, 0, buf)
                self.pack(0x03, 4, buf)
                self.pack(0x00, 8, buf)
                self.pack(real_size, 12, buf)
                self.pack(entry["BinaryType"], 16, buf)
                self.pack(entry["DeviceType"], 20, buf)
                self.pack(entry["PartitionID"], 24, buf)
                self.pack(1 if last else 0, 28, buf)
                self.pack(1 if efs_clear else 0, 32, buf)
                self.pack(1 if update_bootloader else 0, 36, buf)
                self.write(buf)
            self.read(timeout=120000)

    def send_total_bytes(self, size: int):
        """
        Send the total size of the files being flashed.
        Required before flashing.

        Arguments:

        - `size`: Total size in bytes of files
        """
        buf = bytearray(1024)
        self.pack(0x64, 0, buf)
        self.pack(0x02, 4, buf)
        self.pack(size, 8, buf)
        self.write(buf)
        self.read()

    def begin_session(self, resume=False):
        """
        Begin a ODIN session
        
        - `resume`: Are we resuming from a previous session?
        """
        self.session_started = True
        if not resume:
            try:
                self.write("ODIN")
                ret = self.read()
            except USBTimeoutError:
                self.session_started = False
                raise ValueError("Error starting session")
            if ret.tobytes().decode("utf-8") != "LOKE":
                raise ValueError(f"Expected LOKE; Got {ret.tobytes().decode('utf-8')}")
        buf = bytearray(1024)
        self.pack(0x64, 0, buf)
        self.pack(0x00, 4, buf)
        self.pack(0xFFFF, 8, buf)
        self.write(buf)
        ret = self.read()
        version = ret[6]
        logging.debug(f"BL Version: {version}")
        # Send file size
        if version in {0, 1}:
            self.flashpacketsize = 131072
            self.sequencesize = 240
        else:
            self.flashpacketsize = 1048576
            self.sequencesize = 30
        buf = bytearray(1024)
        self.pack(0x64, 0, buf)
        self.pack(0x05, 4, buf)
        self.pack(self.flashpacketsize, 8, buf)
        self.write(buf)
        self.read()
        print("Successfully began a session!")
    def enable_tflash(self):
        """
        Enable T-Flash
        """
        buf = bytearray(1024)
        self.pack(0x64, 0, buf)
        self.pack(0x08, 4, buf)
        self.write(buf)
        self.read(timeout=600000)
        self.t_flash_enabled = True
    def reboot(self):
        """
        Reboot the device
        """
        # Do this to be safe
        self.end_session()
        buf = bytearray(1024)
        self.pack(0x67, 0, buf)
        self.pack(0x01, 4, buf)
        self.write(buf)
        # Reboot
        self.read()
        self.dev = None
        self.partitions = {}

    def shutdown(self):
        """
        Shutdown the device. Doesn't work on some devices.
        """
        buf = bytearray(1024)
        self.pack(0x67, 0, buf)
        self.pack(0x03, 4, buf)
        self.write(buf)
        self.read()
        self.dev = None
        self.partitions = {}

    def end_session(self):
        """
        End ODIN session
        """
        buf = bytearray(1024)
        self.pack(0x67, 0, buf)
        self.pack(0x00, 4, buf)
        self.write(buf)
        self.read()

    def flash_file(self, file: str, partition: str, callback):
        """
        Wrapper around flash function.

        Arguments:

        - `file`: Relative path to file

        - `partition`: Partition to flash

        - `callback`: Progress callback

        !!! example
            ```python
            # This should be structured better...
            from pythor import PyThor
            def callback(percent):
                print(f"{percent}% done")
            FlashTool = PyThor()
            FlashTool.connect()
            FlashTool.begin_session()
            FlashTool.flash_file("/path/to/file", "RECOVERY", callback)
            ```
        """
        # Check if file exists
        if os.path.exists(file):
            with open(file, "rb") as stream:
                self.flash(stream, partition, callback)
        else:
            logging.error("That file doesn't exist")

    def factory_reset(self):
        """
        Factory reset the device (erase userdata)
        """
        buf = bytearray(1024)
        self.pack(0x64, 0, buf)
        self.pack(0x07, 4, buf)
        self.write(buf)
        self.read(timeout=600000)
