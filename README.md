# GBA Compression Tool

Python script to compress data with RLE, LZ77, or Huffman coding to be decompressed by the BIOS.
Usage: `python3 compressor.py input output [method]`
Method can be one of the following:
- `--rle`
- `--lz77`
- `--huffman`
- `--huffman4` (Same as above but each nibble is a symbol instead of each byte)

For LZ77, also add `--vram` if the data should be decompressable to VRAM with the SWI that writes halfwords instead of bytes (disallows `disp = 0`).
Alternatively, just do `python3 compressor.py -h`