#!/usr/bin/env python3

#  Extract bootloader/charging pictures from imagefv blobs
#  Copyright (C) 2026 chickendrop89
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import gzip
import argparse
import tempfile
import shutil
import logging
import io
import traceback

from pathlib import Path
from uefi_firmware.uefi import FirmwareVolume
from uefi_firmware.utils import search_firmware_volumes


def setup_logging() -> logging.Logger:
    """Configure logging"""

    class PrefixFormatter(logging.Formatter):
        def format(self, record):
            record.msg = f"(x) {record.msg}"
            return super().format(record)

    log = logging.getLogger('fastboot-oem-extractor')
    log.setLevel(logging.INFO)
    log.propagate = False

    # Custom prefix
    handler = logging.StreamHandler()
    handler.setFormatter(PrefixFormatter('%(message)s'))
    log.addHandler(handler)

    return log


class ImageExtractor:
    def __init__(self, output_dir: str = 'extracted_images'):
        """Initialize extractor with output directory."""

        self.extracted_files: dict[str, str] = {}
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(
            parents=True, exist_ok=True
        )


    def _get_file_extension(self, header: bytes) -> str:
        """Determine file extension from magic bytes."""

        magic_map = {
            b'\x7fELF': 'elf',
            b'\x1f\x8b': 'gz',
            b'BM': 'bmp',
            b'\x89PNG': 'png',
            b'GIF': 'gif',
            b'PK\x03\x04': 'zip',
            b'BZh': 'bz2',
            b'<?xml': 'xml',
            b'<': 'xml',
        }

        for magic, ext in magic_map.items():
            if header.startswith(magic):
                return ext

        if header[:4] in {b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1'}:
            return 'jpg'

        return 'bin'


    def _extract_and_rename_archive(self, info: dict) -> bool:
        """Extract and rename archive with proper extension."""

        section_data = info['section_data']
        offset = info['offset']
        next_offset = info['next_offset']
        output_path = info['output_path']
        file_label = info['file_label']
        idx = info['idx']

        if not self.extract_gzip_bounded(
            section_data,
            offset,
            next_offset,
            output_path
        ): return False

        try:
            size = output_path.stat().st_size

            with open(output_path, 'rb') as archive_file:
                header = archive_file.read(4)

            ext = self._get_file_extension(header)
            better_path = output_path.parent / f'archive_{idx:04d}.{ext}'
            output_path.rename(better_path)

            logger.info('Extracted archive %3d at offset 0x%08x -> %s (%.1f MB)',
                       idx, offset, better_path.name, size/1024/1024)

            key = f'{file_label}_archive_{idx}'
            self.extracted_files[key] = str(better_path)

            return True
        except OSError as e:
            logger.error('Failed to rename archive at offset 0x%x: %s', offset, e)
            return False


    def _extract_file_label(self, parent_dir: Path) -> str | None:
        """Extract file label from UI section if available."""

        ui_file = parent_dir / 'section0.ui'
        if not ui_file.exists():
            return None

        try:
            with open(ui_file, 'rb') as ui_data_file:
                ui_data = ui_data_file.read()

            if len(ui_data) > 2:
                decoded = ui_data.decode('utf-16-le', errors='ignore')
                file_label = decoded.split('\x00')[0].strip()

                if file_label and len(file_label) > 1:
                    return file_label

        except (OSError, UnicodeDecodeError) as e:
            logger.debug('Could not extract label from %s: %s', ui_file, e)

        return None


    def extract_gzip_stream(self, data: bytes, output_path: Path) -> bool:
        """Extract gzip data efficiently."""

        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gzfile:
                with open(output_path, 'wb') as out:
                    shutil.copyfileobj(
                        gzfile, out,
                        length=1024*1024
                    )

            return True
        except OSError as e:
            logger.warning('Failed to decompress gzip: %s', e)
            return False


    def extract_gzip_bounded(self, data: bytes, start_offset: int,
            next_offset: int | None, output_path: Path) -> bool:
        """Extract single gzip stream bounded by next gzip header."""

        try:
            gzip_data = (data[start_offset:next_offset] if next_offset else data[start_offset:])

            with gzip.GzipFile(fileobj=io.BytesIO(gzip_data)) as gzfile:
                extracted = gzfile.read()

            if len(extracted) > 0:
                with open(output_path, 'wb') as out:
                    out.write(extracted)

                return True
        except (OSError, EOFError) as e:
            logger.debug('Failed to extract gzip at offset 0x%x: %s', start_offset, e)

        return False


    def find_gzip_offsets(self, data: bytes) -> list[int]:
        """Find all gzip stream offsets by scanning for magic bytes."""

        offsets = []
        for i in range(len(data) - 1):
            if data[i:i+2] == b'\x1f\x8b':
                offsets.append(i)

        return offsets


    def process_large_binary_section(self, file_label: str,
            section_data: bytes, output_subdir: Path) -> int:
        """Process large binary sections with multiple gzip archives."""

        raw_path = output_subdir / file_label
        output_subdir.mkdir(
            parents=True, exist_ok=True
        )

        try:
            with open(raw_path, 'wb') as section_file:
                section_file.write(section_data)

            logger.info('Saved: %s (%d bytes)', file_label, len(section_data))
            self.extracted_files[f'{file_label}_raw'] = str(raw_path)
        except OSError as e:
            logger.error('Failed to save %s: %s', file_label, e)
            return 0

        gzip_offsets = self.find_gzip_offsets(section_data)
        logger.info('Found %d gzip stream(s) in %s', len(gzip_offsets), file_label)

        if not gzip_offsets:
            return 0

        archives_dir = output_subdir / 'images'
        archives_dir.mkdir(
            parents=True, exist_ok=True
        )

        extracted_count = 0
        for idx, offset in enumerate(gzip_offsets):
            next_offset = (gzip_offsets[idx + 1] if idx + 1 < len(gzip_offsets) else None)
            output_path = archives_dir / f'archive_{idx:04d}.bin'

            archive_info = {
                'section_data': section_data,
                'offset': offset,
                'next_offset': next_offset,
                'output_path': output_path,
                'file_label': file_label,
                'idx': idx,
            }

            if self._extract_and_rename_archive(archive_info):
                extracted_count += 1
            else:
                logger.debug('Could not extract gzip at offset 0x%x', offset)

        logger.info('Successfully extracted %d archive(s) from %s', extracted_count, file_label)
        return extracted_count


    def extract_from_section(self, file_label: str, section_data: bytes,
            output_subdir: Path) -> str | None:
        """Extract images from firmware file section."""

        output_subdir.mkdir(parents=True, exist_ok=True)

        if file_label.endswith('.bmp') or len(section_data) < 1024*1024:
            output_path = output_subdir / file_label
            try:
                with open(output_path, 'wb') as output_file:
                    output_file.write(section_data)

                logger.info('Extracted: %s (%d bytes)', file_label, len(section_data))
                return str(output_path)
            except OSError as e:
                logger.error('Failed to save %s: %s', file_label, e)
                return None

        if len(section_data) >= 2 and section_data[0:2] == b'\x1f\x8b':
            extracted_path = output_subdir / f'{file_label}.extracted'

            logger.info('Detected gzip compression in %s', file_label)

            if self.extract_gzip_stream(section_data, extracted_path):
                size = extracted_path.stat().st_size

                logger.info('Extracted: %s (gzip) -> %s (%d bytes)', file_label, extracted_path.name, size)
                return str(extracted_path)

        if len(section_data) > 1024*1024:
            gzip_offsets = self.find_gzip_offsets(section_data)

            if len(gzip_offsets) > 1:
                logger.info('Detected multiple gzip streams in %s, extracting all...', file_label)
                self.process_large_binary_section(
                    file_label,
                    section_data,
                    output_subdir
                )

                return str(output_subdir / file_label)

        output_path = output_subdir / file_label
        try:
            with open(output_path, 'wb') as output_file:
                output_file.write(section_data)

            logger.info('Extracted: %s (raw) (%d bytes)', file_label, len(section_data))
            return str(output_path)
        except OSError as e:
            logger.error('Failed to save %s: %s', file_label, e)
            return None


    def process_raw_files(self, dump_dir: Path) -> None:
        """Find and process .raw files from dump directory."""

        for raw_file in dump_dir.rglob('*.raw'):
            try:
                parent_dir = raw_file.parent.name

                if not parent_dir.startswith('file-'):
                    continue

                file_label = self._extract_file_label(raw_file.parent)

                if not file_label or len(file_label) <= 1:
                    file_label = parent_dir[5:].lower()

                with open(raw_file, 'rb') as raw_data_file:
                    section_data = raw_data_file.read()

                output_subdir = self.output_dir / file_label
                extracted = self.extract_from_section(
                    file_label,
                    section_data,
                    output_subdir
                )

                if extracted:
                    self.extracted_files[file_label] = extracted
            except OSError as e:
                logger.warning('Error processing %s: %s', raw_file, e)


    def extract_from_elf(self, elf_path: str) -> bool:
        """Extract images from ELF file containing UEFI firmware."""

        elf_path = Path(elf_path)
        if not elf_path.exists():
            logger.error('File not found: %s', elf_path)
            return False

        try:
            with open(elf_path, 'rb') as elf_data_file:
                data = elf_data_file.read()

            volumes = search_firmware_volumes(data)

            if not volumes:
                logger.error('No UEFI firmware volumes found in file')
                return False

            logger.info('Found %d firmware volume(s)', len(volumes))

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)

                for offset in volumes:
                    logger.info('Processing FV at offset: 0x%x', offset)
                    try:
                        fv = FirmwareVolume(data[offset-40:], name=f'0x{offset-40:x}')

                        if fv.valid_header:
                            logger.info('FV Size: %d bytes', fv.size)

                            if fv.process():
                                logger.info('FV processed successfully, dumping files...')
                                fv.dump(str(tmpdir_path))
                                self.process_raw_files(tmpdir_path)
                            else:
                                logger.warning('Failed to process FV at 0x%x', offset)
                        else:
                            logger.debug('Invalid FV header at offset 0x%x', offset)
                    except OSError as e:
                        logger.warning('Error processing FV at offset 0x%x: %s', offset, e)

            return len(self.extracted_files) > 0

        except OSError as e:
            logger.error('Error processing ELF: %s', e)
            traceback.print_exc()
            return False


def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description='Extract bootloader/charging pictures from imagefv blobs',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        'input',
        help='Input file to process',
        nargs='+'
    )
    parser.add_argument(
        '-o', '--output',
        default='extracted_images',
        help='Output directory'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    for input_file in args.input:   
        logger.info('Processing: %s', input_file)
        logger.info('Output directory: %s', args.output)

        if len(args.input) > 1:
            device_name = Path(input_file).stem
            output_dir = os.path.join(args.output, device_name)
        else:
            output_dir = args.output

        extractor = ImageExtractor(output_dir)

        if extractor.extract_from_elf(input_file):
            logger.info('Successfully extracted %d item(s)', len(extractor.extracted_files))
        else:
            logger.error('Failed to extract images from %s', input_file)

    logger.info('Extraction complete!')

logger = setup_logging()

if __name__ == '__main__':
    main()
