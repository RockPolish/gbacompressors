import argparse
import os
import sys
import collections


class BIOS_RLE_RLEBlock():
    def __init__(self, byte, length):
        assert 3 <= length <= 130
        self.byte = byte
        self.length = length
    
    def output(self, f):
        flag_byte = 0x80 | (self.length - 3)
        f.write(flag_byte.to_bytes(1, "little"))
        f.write(self.byte.to_bytes(1, "little"))
    
    def __str__(self):
        return f"RLEBlock({self.length} x {self.byte:02x})"


class BIOS_RLE_RawBlock():
    def __init__(self, data):
        assert 1 <= len(data) <= 128
        self.data = data
    
    def output(self, f):
        flag_byte = len(self.data) - 1
        f.write(flag_byte.to_bytes(1, "little"))
        for byte in self.data:
            f.write(byte.to_bytes(1, "little"))

    def __str__(self):
        return "RawBlock(" + " ".join(f"{byte:02x}" for byte in self.data) + ")"


class BIOS_RLE():
    def __init__(self, inb, vram):
        self.inb = inb
        self.inl = len(inb)     
    
    def compress(self):
        self.blocks = []  # list of RLE or uncompressed blocks
        self.buffer = []  # buffer for uncompressed bytes
        i = 0
 
        """
        Data header (32bit)
          Bit 0-3   Reserved
          Bit 4-7   Compressed type (must be 3 for run-length)
          Bit 8-31  Size of decompressed data
        Repeat below. Each Flag Byte followed by one or more Data Bytes.
        Flag data (8bit)
          Bit 0-6   Expanded Data Length (uncompressed N-1, compressed N-3)
          Bit 7     Flag (0=uncompressed, 1=compressed)
        Data Byte(s) - N uncompressed bytes, or 1 byte repeated N times
        """

        while i < self.inl:
            cur = self.inb[i]
            match = 1
            for j in range(129): # RLE can be up to 130 bytes long, so current byte + 129
                pos = i+j+1
                if pos >= self.inl:
                    break
                if self.inb[pos] == cur:
                    match += 1
                else:
                    break
            
            if match >= 3: # RLE should be at least 3 bytes
                self.flush_buffer() # write the current uncompressed bytes as an uncompressed block
                self.blocks.append(BIOS_RLE_RLEBlock(cur, match))
                i += match
            else:
                self.buffer.append(cur)
                if len(self.buffer) == 128: # maximum uncompressed length = 128 bytes
                    self.flush_buffer()
                i += 1
        
        self.flush_buffer()
        
    def flush_buffer(self):
        if len(self.buffer) > 0:
            self.blocks.append(BIOS_RLE_RawBlock(self.buffer.copy()))
            self.buffer = []
    
    def output(self, f):
        header = (3 << 4) | (self.inl << 8)
        f.write(header.to_bytes(4, "little"))
        
        for b in self.blocks:
            b.output(f)


#######################################


class BIOS_LZ77_RawBlock():
    def __init__(self, data):
        self.data = data # one byte
    
    def output(self, f):
        f.write(self.data.to_bytes(1, "little"))

    def __str__(self):
        return f"RawBlock({self.data:02x})"


class BIOS_LZ77_BackReferenceBlock():
    def __init__(self, disp, length):
        self.disp = disp
        self.length = length
    
    def output(self, f):
        assert 0 <= self.disp < 4096
        assert 3 <= self.length <= 18
        f.write((((self.length-3) << 4) | (self.disp >> 8)).to_bytes(1, "little"))
        f.write((self.disp & 0xFF).to_bytes(1, "little"))

    def __str__(self):
        return f"BackReferenceBlock(len={self.length}, disp={self.disp})"


class BIOS_LZ77:
    def __init__(self, inb, vram):
        self.inb = inb
        self.inl = len(inb)
        self.vram = vram

    def compress(self):
        self.blocks = []  # list of compressed blocks, uncompressed bytes
        i = 0
    
        """
        Data header (32bit)
          Bit 0-3   Reserved (0)
          Bit 4-7   Compressed type (must be 1 for LZ77)
          Bit 8-31  Size of decompressed data
        Repeat below. Each Flag Byte followed by eight Blocks.
        Flag data (8bit)
          Bit 0-7   Type Flags for next 8 Blocks, MSB first
        Block Type 0 - Uncompressed - Copy 1 Byte from Source to Dest
          Bit 0-7   One data byte to be copied to dest
        Block Type 1 - Compressed - Copy N+3 Bytes from Dest-Disp-1 to Dest
          Bit 0-3   Disp MSBs
          Bit 4-7   Number of bytes to copy (minus 3)
          Bit 8-15  Disp LSBs
        """
        
        while i < self.inl:
            cur = self.inb[i]
            # look for match
            best_match = (2, -1) # (length, disp)
            
            # this following part could be sped up
            for disp in range(1 if self.vram else 0, 4096):
                match_len = 0
                for j in range(18):
                    if not (0 <= (i-disp-1+j) < self.inl):
                        break
                    
                    if (i+j) >= self.inl:
                        break
                
                    if self.inb[i-disp-1+j] == self.inb[i+j]:
                        match_len += 1
                    else:
                        break
                if match_len > best_match[0]:
                    best_match = (match_len, disp)
                    if match_len == 18: # 18 is the maximum length so we don't need to keep searching for a better one
                        break
            
            if best_match[1] == -1:
                # no match
                self.blocks.append(BIOS_LZ77_RawBlock(cur))
                i += 1
            else:
                # match
                self.blocks.append(BIOS_LZ77_BackReferenceBlock(best_match[1], best_match[0]))
                i += best_match[0]

    def output(self, f):
        header = (1 << 4) | (self.inl << 8)
        f.write(header.to_bytes(4, "little"))
        
        for i, b in enumerate(self.blocks):
            if (i % 8) == 0:
                # flag byte for the next 8 blocks
                flag = 0
                for j, b2 in enumerate(self.blocks[i:i+8]):
                    # MSB first, bit = 1 for compressed
                    if type(b2) == BIOS_LZ77_BackReferenceBlock:
                        flag |= 1 << (7-j)
                f.write(flag.to_bytes(1, "little"))
            b.output(f)



#######################################



# Queue of (weight, item) tuples, get() returns the tuple with the lowest weight and removes it
class PriorityQueue():
    def __init__(self):
        self.q = []

    def put(self, t):
        self.q.append(t)
        self.q = sorted(self.q, key=lambda x: x[0])

    def qsize(self):
        return len(self.q)

    def get(self):
        assert len(self.q) > 0
        pop = self.q[0]
        self.q = self.q[1:]
        return pop

class LeafNode():
    def __init__(self, symbol, weight):
        self.symbol = symbol
        self.weight = weight
        self.encoding = ""

class InnerNode():
    def __init__(self, child0=None, child1=None):
        self.child0 = child0
        self.child1 = child1
        if child0 is not None and child1 is not None:
            self.weight = child0.weight + child1.weight
        else:
            self.weight = None

# loop over the nodes and get the leaf nodes the correct encoding (string of zeroes and ones)
def set_encoding(node, prefix):
    if type(node) == InnerNode:
        set_encoding(node.child0, prefix+"0")
        set_encoding(node.child1, prefix+"1")
    else:
        node.encoding = prefix


def construct_tree(inp):
    leaves = [LeafNode(x[0], x[1]) for x in inp]
    prio = PriorityQueue()
    for node in leaves:
        prio.put((node.weight, node))

    while prio.qsize() > 1:
        # merge the two nodes with the lowest weight
        pop0 = prio.get()[1]
        pop1 = prio.get()[1]
        join = InnerNode(pop0, pop1)
        prio.put((join.weight, join))

    assert prio.qsize() == 1
    root = prio.get()[1]
    assert prio.qsize() == 0

    set_encoding(root, "")

    convdict = dict()
    for l in leaves:
        convdict[l.symbol] = l.encoding

    return root, leaves, convdict



class BIOS_Huffman:
    def __init__(self, inb, vram, symbolsize):
        self.inb = inb
        self.inl = len(inb)
        self.symbolsize = symbolsize # 4 or 8 bits

    def compress(self):
        i = 0
    
        """
        Data Header (32bit)
          Bit0-3   Data size in bit units (normally 4 or 8)
          Bit4-7   Compressed type (must be 2 for Huffman)
          Bit8-31  24bit size of decompressed data in bytes
        Tree Size (8bit)
          Bit0-7   Size of Tree Table/2-1 (ie. Offset to Compressed Bitstream)
        Tree Table (list of 8bit nodes, starting with the root node)
         Root Node and Non-Data-Child Nodes are:
          Bit0-5   Offset to next child node,
                   Next child node0 is at (CurrentAddr AND NOT 1)+Offset*2+2
                   Next child node1 is at (CurrentAddr AND NOT 1)+Offset*2+2+1
          Bit6     Node1 End Flag (1=Next child node is data)
          Bit7     Node0 End Flag (1=Next child node is data)
         Data nodes are (when End Flag was set in parent node):
          Bit0-7   Data (upper bits should be zero if Data Size is less than 8)
        Compressed Bitstream (stored in units of 32bits)
          Bit0-31  Node Bits (Bit31=First Bit)  (0=Node0, 1=Node1)
        """
        
        # make a tree first
        counter = collections.Counter(self.inb)
        self.root, self.leaves, self.convdict = construct_tree(counter.items())
        self.bits = []
        
        #print(self.convdict)
        
        # just create a big string of bits
        for cur in self.inb:
            self.bits += self.convdict[cur]
    
    def output(self, f):
        header = self.symbolsize | (2 << 4) | (self.inl << 8)
        f.write(header.to_bytes(4, "little"))
        
        # tree size = # of leaves minus one, don't do -1 if the # is odd because we insert two more bytes below in that case to retain word alignment
        f.write((len(self.leaves) - 1 + (len(self.leaves) % 1)).to_bytes(1, "little"))
        
        # do breadth-first search over the tree
        todo = [self.root]
        fully = [self.root] # all nodes in breadth-first order
        while len(todo) > 0:
            pop = todo[0]
            todo = todo[1:]
            if type(pop) == InnerNode:
                todo.append(pop.child0)
                todo.append(pop.child1)
                fully.append(pop.child0)
                fully.append(pop.child1)
        
        
        for i, node in enumerate(fully):
            if type(node) == InnerNode:
                child0pos = fully.index(node.child0)
                child1pos = fully.index(node.child1)
                assert child0pos + 1 == child1pos # my child0 and child1 should be next to each other
                offs = (child1pos - ((i + 1) & ~1) - 2)//2
                assert 0 <= offs < 0x40  # offset should fit in 6 bits, if this assert fails, your tree is too wide (large & relatively balanced)

                if type(node.child0) == LeafNode:
                    offs |= 0x80
                if type(node.child1) == LeafNode:
                    offs |= 0x40

                f.write(offs.to_bytes(1, byteorder="little"))
            else:
                f.write(node.symbol.to_bytes(1, byteorder="little"))
        
        if ((len(fully)+1) % 4) == 2:
            # need to add two bytes so that the next word is word aligned
            f.write((0).to_bytes(2, byteorder="little"))
        
        # go over self.bits, turn it into words (bit 31 first)
        word = 0
        bitpos = 31
        for char in self.bits:
            assert char in {"0", "1"}
            if char == "1":
                word |= 1 << bitpos
            
            bitpos -= 1
            if bitpos == -1:
                f.write(word.to_bytes(4, byteorder="little"))
                word = 0
                bitpos = 31
        
        if bitpos < 31:
            f.write(word.to_bytes(4, byteorder="little"))


#######################################


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("inp", help="Input file")
    parser.add_argument("outp", help="Output file")
  
    parser.add_argument("-rle", "--rle", action="store_true", help="Use the BIOS RLE format")
    parser.add_argument("-lz77", "--lz77", action="store_true", help="Use the BIOS LZ77 format")
    parser.add_argument("-huffman", "--huffman", action="store_true", help="Use the BIOS Huffman format (8-bit symbols)")
    parser.add_argument("-huffman4", "--huffman4", action="store_true", help="Use the BIOS Huffman format (4-bit symbols)")
    
    parser.add_argument("-v", "--vram", action="store_true", help="Ensure 16-bit routines can decompress it (only affects LZ77)")
    args = parser.parse_args()
    
    f = open(args.inp, "rb")
    inb = list(f.read())
    f.close()
    
    if len(inb) == 0:
        print("Input is empty, aborting")
        sys.exit(-1)
      
    if (len(inb) % 4) != 0:
        print("Warning: input length is not a multiple of 4, padding with zeroes")
        inb += (4-(len(inb) % 4)) * [0]
    
    if args.rle:
        obj = BIOS_RLE(inb, args.vram)
    elif args.lz77:
        obj = BIOS_LZ77(inb, args.vram)
    elif args.huffman:
        obj = BIOS_Huffman(inb, args.vram, 8)
    elif args.huffman4:
        # turn list of bytes into list of nibbles, then continue normally
        inb2 = []
        for byte in inb:
            inb2.append(byte & 0xF)
            inb2.append(byte >> 4)
        
        obj = BIOS_Huffman(inb2, args.vram, 4)
    else:
        print("No compression method selected, aborting")
        sys.exit(-1)
    
    obj.compress()
    
    f = open(args.outp, "wb")
    obj.output(f)
    f.close()
    
    print("Compressed", len(inb), "bytes to", os.path.getsize(args.outp), "bytes")


if __name__ == "__main__":
    main()
