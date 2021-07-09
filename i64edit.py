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

def pack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start+count])

class BytesReader:
    def __init__(self, data):
        self.data = bytearray(data)
        self.pos = 0
        self.coverage = [False] * len(data)

    def tell(self):
        return self.pos

    def get_coverage(self):
        ret = ''
        state = -1
        count = 0
        for e in self.coverage:
            if e != state:
                if count:
                    ret += f'{state}*{count}, '
                state = e
                count = 1
            else:
                count += 1
        ret += f'{e}*{count}'
        return ret

    def read(self, count):
        end = self.pos + count
        if end > len(self.data):
            raise Exception("read overrun")
        ret = self.data[self.pos:end]
        self.coverage[self.pos:end] = [True] * (end - self.pos)
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
            raise NotImplementedError("read overrun")
        self.data[off:end] = newbytes

class BytesWriter:
    def __init__(self, size):
        self.data = bytearray(size)
        self.pos = 0
        self.coverage = [False] * size

    def get_coverage(self):
        ret = ''
        state = -1
        count = 0
        for e in self.coverage:
            if e != state:
                if count:
                    ret += f'{state}*{count}, '
                state = e
                count = 1
            else:
                count += 1
        ret += f'{e}*{count}'
        return ret

    def write(self, bin):
        start = self.pos
        end = self.pos + len(bin)
        if end > len(self.data):
            raise NotImplementedError("write overrun")
        self.data[start:end] = bin
        self.pos = end
        self.coverage[start:end] = [True] * (end - start)

    def writes(self, fmt, *args):
        fmt = '=' + fmt
        self.write(struct.pack(fmt, *args))

    def seek(self, off):
        self.pos = off

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

class Entry:
    def __init__(self, i):
        self.i = i
        self.recofs = None
        self.keylen = None
        self.rawkey = None
        self.key = None
        self.vallen = None
        self.val = None

    def read_data(self, args, br: BytesReader, prevkey):
        br.seek(self.recofs)
        self.keylen = br.reads("H")
        self.rawkey = br.read(self.keylen)
        self.vallen = br.reads("H")
        self.val = br.read(self.vallen)

    def write_data(self, bw: BytesWriter):
        bw.seek(self.recofs)
        bw.writes("H", self.keylen)
        bw.write(self.rawkey)
        bw.writes("H", self.vallen)
        bw.write(self.val)

    def modify(self, args, acc):
        """Changes entry value, accumulates length changes,
        moves the entry backwards on page according to accumulated change"""
        if self.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
            oldbytes = b'\x00' + args.rename[0].encode('utf-8')
            if self.val.startswith(oldbytes):
                newbytes = b'\x00' + args.rename[1].encode('utf-8')
                print(f'modify entry {self.i}')
                len_change = len(newbytes) - len(oldbytes)
                self.val = newbytes + self.val[len(oldbytes):]
                self.vallen += len_change
                acc += len_change
        self.recofs -= acc
        return acc


    def print_if(self):
        if self.key.startswith(b'N$ dirtree/funcs'):
            print(self.__class__, 'N$ dirtree/funcs', hexdump(self.key), hexdump(self.val))
        if self.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
            print(self.__class__, 'funcdir', hexdump(self.key), hexdump(self.val[:16]),
                  self.recofs, '...', self.recofs + 2 + self.keylen + 2 + self.vallen)


class IndexEntry(Entry):
    def __init__(self, br: BytesReader, i):
        super().__init__(i)
        self.npage, self.recofs = br.reads("LH")

    def write_head(self, bw: BytesWriter):
        bw.writes("LH", self.npage, self.recofs)

    def read_data(self, args, br: BytesReader, prevkey):
        super().read_data(args, br, prevkey)
        self.key = self.rawkey
        self.print_if()


class LeafEntry(Entry):
    def __init__(self, bh: BytesReader, i):
        super().__init__(i)
        self.indent, self.unk, self.recofs = bh.reads("HHH")

    def write_head(self, bw: BytesWriter):
        bw.writes("HHH", self.indent, self.unk, self.recofs)

    def read_data(self, args, br, prevkey):
        super().read_data(args, br, prevkey)
        self.key = prevkey[:self.indent] + self.rawkey
        self.print_if()


class Page:
    def __init__(self, args, fh: FileHandler):
        self.ofs = fh.f.tell()
        br = BytesReader(fh.f.read(0x2000))
        self.preceding, self.entrycount = br.reads("LH")
        if self.entrycount == 0 or not args.rename:
            fh.fo.write(br.data)
            return

        entryType = LeafEntry
        if self.preceding:
            entryType = IndexEntry

        self.entries = []
        for i in range(self.entrycount):
            ent = entryType(br, i)
            self.entries.append(ent)
        self.unk, self.datastart = br.reads("LH")
        self.free_bytes = self.datastart - br.tell()

        prevkey = b''
        for ent in self.entries:
            ent.read_data(args, br, prevkey)
            prevkey = ent.key
            if ent.recofs < self.datastart:
                raise NotImplementedError("unexpected entry data before page.datastart")

        bw = self.rebuild(args)
        fh.fo.write(bw.data)


    def rebuild(self, args):
        move = 0
        for ent in sorted(self.entries, key=lambda e: e.recofs, reverse=True):
            move = ent.modify(args, move)
        if move > self.free_bytes:
            raise NotImplementedError("no more space in this page")

        bw = BytesWriter(0x2000)
        bw.writes("LH", self.preceding, self.entrycount)

        for ent in self.entries:
            ent.write_head(bw)

        self.datastart -= move
        bw.writes("LH", self.unk, self.datastart)
        for ent in self.entries:
            ent.write_data(bw)

        return bw


class IDBFile:
    def __init__(self, fh: FileHandler):
        self.fh = fh
        magic = fh.read(6)
        if not magic.startswith(b"IDA2"):
            raise Exception("invalid file format")

        values = fh.reads("QQLLHQQQ5LQL")
        self.offsets = [values[_] for _ in (0, 1, 5, 6, 7, 13)]
        self.checksums = [values[_] for _ in (8, 9, 10, 11, 12, 14)]

        rest = self.offsets[0] - fh.tell()
        self.fh.read(rest)


    def read_id0(self, args):
        compressed, sectlen = self.fh.reads("BQ")
        self.fh.read(0x2000)
        pagecount = sectlen // 0x2000 - 1
        print(f'reading id0: {pagecount = }')

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

  i64edit in.i64 out.i64 --rename FolderNameBad FolderNameGood
""")
    parser.add_argument("srcfile")
    parser.add_argument("destfile")
    parser.add_argument('--rename', nargs=2, help='search and replace in folder names')
    args = parser.parse_args()

    processfile(args)

