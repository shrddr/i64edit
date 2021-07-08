import argparse
import binascii
import struct

def hexdump(data):
    if data is None:
        return
    return binascii.b2a_hex(data).decode('utf-8')

def unpack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start+count])

class BytesHandler:
    def __init__(self, data):
        self.data = bytearray(data)
        self.pos = 0

    def read(self, count):
        end = self.pos + count
        if end > len(self.data):
            raise Exception("bytes read over len")
        ret = self.data[self.pos:end]
        self.pos = end
        return ret

    def reads(self, fmt):
        fmt = '=' + fmt
        count = struct.calcsize(fmt)
        data = self.read(count)
        ret = struct.unpack(fmt, data)
        if type(ret) == tuple and len(ret) == 1:
            return ret[0]
        return ret

    def seek(self, off):
        self.pos = off

    def modify(self, off, newbytes):
        end = off+len(newbytes)
        if end > len(self.data):
            raise NotImplementedError("overrun")
        self.data[off:end] = newbytes

    def replace(self, start, end, newbytes):
        end = off+len(src)
        if end > len(self.data):
            raise NotImplementedError("overrun")
        self.data[off:end] = src

class FileHandler:
    def __init__(self, src, dst):
        self.f = open(src, "rb")
        self.fo = open(dst, "wb")

    def read(self, count):
        bs = self.f.read(count)
        self.fo.write(bs)
        return bs

    def reads(self, fmt):
        fmt = '=' + fmt
        i = struct.calcsize(fmt)
        bs = self.f.read(i)
        self.fo.write(bs)
        ret = struct.unpack(fmt, bs)
        if type(ret) == tuple and len(ret) == 1:
            return ret[0]
        return ret

    def seek(self, off):
        rest = off - self.tell()
        if rest < 0:
            raise ValueError("negative seek")
        self.read(rest)

    def tell(self):
        return self.f.tell()


class IndexEntry:
    def __init__(self, bh: BytesHandler):
        self.npage, self.recofs = bh.reads("LH")

    def read(self, args, bh, prevkey):
        bh.seek(self.recofs)
        keylen = bh.reads("H")
        self.key = bh.read(keylen)
        vallen = bh.reads("H")
        self.val = bh.read(vallen)
        handleEntry(args, bh, self)

class LeafEntry:
    def __init__(self, bh: BytesHandler):
        self.indent, unk, self.recofs = bh.reads("HHH")

    def read(self, args, bh, prevkey):
        bh.seek(self.recofs)
        keylen = bh.reads("H")
        key = bh.read(keylen)
        self.key = prevkey[:self.indent] + key
        vallen = bh.reads("H")
        self.val = bh.read(vallen)
        handleEntry(args, bh, self)

def handleEntry(args, bh: BytesHandler, entry):
    # if entry.key.startswith(b'N$ dirtree/funcs'):
    #     print('N$ dirtree/funcs', hexdump(entry.key), hexdump(entry.val))
    # if entry.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
    #     print('funcdir', hexdump(entry.key), hexdump(entry.val))
    if entry.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
        if args.rename:
            oldbytes = b'\x00' + args.rename[0].encode('utf-8')
            if entry.val.startswith(oldbytes):
                newbytes = b'\x00' + args.rename[1].encode('utf-8')
                if len(newbytes) != len(oldbytes):
                    raise NotImplementedError()
                bh.modify(bh.pos-len(entry.val), newbytes)


class Page:
    def __init__(self, args, fh: FileHandler):
        bh = BytesHandler(fh.f.read(0x2000))
        preceding, entrycount = bh.reads("LH")

        entryType = LeafEntry
        if preceding:
            entryType = IndexEntry

        entries = []
        for i in range(entrycount):
            ent = entryType(bh)
            entries.append(ent)

        prevkey = b''
        for ent in entries:
            ent.read(args, bh, prevkey)
            prevkey = ent.key


        fh.fo.write(bh.data)

class IDBFile:
    def __init__(self, fh: FileHandler):
        self.fh = fh
        magic = fh.read(6)
        if not magic.startswith(b"IDA2"):
            raise Exception("invalid file")

        values = fh.reads("QQLLHQQQ5LQL")
        self.offsets = [values[_] for _ in (0, 1, 5, 6, 7, 13)]
        self.checksums = [values[_] for _ in (8, 9, 10, 11, 12, 14)]

        rest = self.offsets[0] - fh.tell()
        self.fh.read(rest)


    def read_id0(self, args):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(0x2000)
        pagecount = sectlen // 0x2000 - 1
        print('reading id0', pagecount, 'pages')

        pages = []
        for i in range(pagecount):
            print('reading page', i)
            pages.append(Page(args, self.fh))

    def read_id1(self):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(sectlen)

    def read_nam(self):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(sectlen)

    def read_til(self):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(sectlen)

    def read_seg(self):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(sectlen)


def processfile(args):
    fh = FileHandler(args.srcfile, args.destfile)
    idb = IDBFile(fh)
    idb.read_id0(args)
    idb.read_id1()
    idb.read_nam()
    idb.read_til()
    idb.read_seg()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(epilog="""
Makes a copy of .i64 file with certain modifications.

Examples:

  i64edit in.i64 out.i64 --rename CRijndael CRixndael
""")
    parser.add_argument("srcfile")
    parser.add_argument("destfile")
    parser.add_argument('--rename', nargs=2, help='search and replace in folder names')
    args = parser.parse_args()

    processfile(args)

