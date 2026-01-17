# imagefv-extractor
Extract bootloader/charging pictures from `imagefv` blobs

## How to use:
1. Install python requirements
```shell
pip install -r requirements.txt
```

2. Prepare your `imagefv` blob from the internet, or by pulling it from the device via `adb`
3. Run `extractor.py` against the blob:
```shell
python ./extractor.py ./imagefv.elf
```

## How it works:
1. The script scans the input file for `UEFI` structures using the `uefi-firmware` package
2. If it finds them, it dumps all files/sections to a temporary directory
3. Then it identifies the dumped files for known types (defined in `magic_map` struct) and organizes them
4. And also if found, any files containing GZip archives (most commonly `logo.img`) are extracted

## Is it possible to repackage it, and create custom splashes?
During investigation, i have noticed the `imagefv` blob contains some CoT certificates, 
so it is absolutely possible that this partition is verified during the `XBL` phase,
causing the device not to boot if it is altered.

```
DECIMAL       HEXADECIMAL     DESCRIPTION
--------------------------------------------------------------------------------
0             0x0             ELF, 32-bit LSB executable, ARM, version 1 (SYSV)
4096          0x1000          UEFI PI Firmware Volume, volume size: 6291456, header size: 0, revision: 0, EFI Firmware File System v2, GUID: 8C8CE578-8A3D-4F1C-3599-896185C32DD3
4216          0x1078          LZMA compressed data, properties: 0x5D, dictionary size: 16777216 bytes, uncompressed size: 7237640 bytes
6296088       0x601218        Certificate in DER format (x509 v3), header length: 4, sequence length: 596
6296688       0x601470        Certificate in DER format (x509 v3), header length: 4, sequence length: 685
6297377       0x601721        Certificate in DER format (x509 v3), header length: 4, sequence length: 720
```

And i could not find any open-source (re)packaging tool for it either, so it would probably need to be made.
I am not risking it at this moment due to the absence of patched firehose for my device.

Here are some references to it being loaded on `xiaomi-amethyst` during `ABL` phase:
```
GetAdjustLogoIndexpDisplayInfo->uDisplayWidth 1220: 2712, country_info = 1, uLogoIndex = 0, realLogoIndex = 128!
Display_Utils_RenderSplashScreen: load imagefv name is: logo.img
LoadCompressedImageFromLogoData: Setvariable Logo Image returned eStatus: Success, LogoMagic: LOGO!!!
LoadCompressedImageFromLogoData: LogoCompressedBlockNum:9 LogoCompressedBytesNum:35458 
Display_Utils_RenderSplashScreen: CompressedData size is: 35458 logo index = 128
DeCompressLogoData: DecompressBufferSize:9925976  ScratchSize:131072
DeCompressLogoData: logo decompress done, use time: 19ms
Display_Utils_RenderSplashScreen: OEM Logo Successfully Loaded
```

## Requirements
- Python 3.10 or newer
- Installed `uefi_firmware` pip package
