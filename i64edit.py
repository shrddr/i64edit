import argparse
import binascii
import struct
from shutil import copyfile


def hexdump(data):
    if data is None:
        return
    return binascii.b2a_hex(data).decode('utf-8')


def unpack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start + count])


def pack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start + count])


def binary_search(a, k):
    first, last = 0, len(a)
    while first < last:
        mid = (first + last) >> 1
        if k < a[mid].key:
            last = mid
        else:
            first = mid + 1
    return first - 1


class IdaUnpacker:
    def __init__(self, data):
        self.data = data
        self.o = 0

    def eof(self):
        return self.o >= len(self.data)

    def next64(self):
        if self.eof():
            return None
        lo = self.next32()
        hi = self.next32()
        return (hi << 32) | lo

    def next64signed(self):
        val = self.next64()
        if val < 0x8000000000000000:
            return val
        return val - 0x10000000000000000

    def next32(self):
        if self.eof():
            return None
        byte = self.data[self.o:self.o+1]
        if byte == b'\xff':
            # a 32 bit value:
            # 1111 1111 xxxx xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            if self.o+5 > len(self.data):
                return None
            val, = struct.unpack_from(">L", self.data, self.o+1)
            self.o += 5
            return val
        elif byte < b'\x80':
            # a 7 bit value:
            # 0xxx xxxx
            self.o += 1
            val, = struct.unpack("B", byte)
            return val
        elif byte < b'\xc0':
            # a 14 bit value:
            # 10xx xxxx xxxx xxxx
            if self.o+2 > len(self.data):
                return None
            val, = struct.unpack_from(">H", self.data, self.o)
            self.o += 2
            return val&0x3FFF
        elif byte < b'\xe0':
            # a 29 bit value:
            # 110x xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            if self.o+4 > len(self.data):
                return None
            val, = struct.unpack_from(">L", self.data, self.o)
            self.o += 4
            return val&0x1FFFFFFF
        else:
            return None


class IdaPacker:
    def __init__(self):
        self.data = bytearray()

    def push64(self, val):
        lo = val & 0xFFFFFFFF
        hi = val >> 32
        self.push32(lo)
        self.push32(hi)

    def push64signed(self, val):
        if val < 0:
            val = val + 0x10000000000000000
        self.push64(val)

    def push32(self, val):
        if val < 0x80:
            # a 7 bit value:
            # 0xxx xxxx
            b = struct.pack("B", val)
        elif val < 0x4000:
            # a 14 bit value:
            # 10xx xxxx xxxx xxxx
            val |= 0x8000
            b = struct.pack(">H", val)
        elif val < 0x20000000:
            # a 29 bit value:
            # 110x xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            val |= 0x80000000
            b = struct.pack(">L", val)
        else:
            # a 32 bit value:
            # 1111 1111 xxxx xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            b = struct.pack(">BI", 0xFF, val)
        self.data += b


class BytesReader:
    def __init__(self, data):
        self.data = data
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
        end = off + len(newbytes)
        if end > len(self.data):
            raise NotImplementedError("read overrun")
        self.data[off:end] = newbytes

class BytesWriter:
    def __init__(self, other):
        self.data = bytearray(other)
        self.pos = 0
        self.coverage = [False] * len(self.data)

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
        self.fo = open(dst, "r+b")

    def read(self, count):
        bs = self.f.read(count)
        return bs

    def reads(self, fmt):
        fmt = '=' + fmt
        i = struct.calcsize(fmt)
        bs = self.f.read(i)
        ret = struct.unpack(fmt, bs)
        if type(ret) == tuple and len(ret) == 1:
            return ret[0]
        return ret

    def seek(self, off):
        self.f.seek(off, 0)

    def tell(self):
        return self.f.tell()

    def close(self):
        self.f.close()
        self.fo.close()


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
        # if self.key.startswith(b'N$ dirtree/funcs'):
        #     print(self.__class__, 'N$ dirtree/funcs', hexdump(self.key), hexdump(self.val))
        # if self.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
        #     print(self.__class__, 'funcdir', hexdump(self.key), hexdump(self.val[:16]),
        #           self.recofs, '...', self.recofs + 2 + self.keylen + 2 + self.vallen)
        pass

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
    def __init__(self, fh: FileHandler, pagesize):
        self.fh = fh
        self.offset = fh.f.tell()
        br = BytesReader(fh.f.read(pagesize))

        self.br = br
        self.preceding, self.entrycount = br.reads("LH")
        # if self.entrycount == 0 or not args.rename:
        #     fh.fo.write(br.data)
        #     return

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

        # bw = self.rebuild(args, br)
        # fh.fo.write(bw.data)

    def isindex(self):
        return self.preceding != 0

    def isleaf(self):
        return self.preceding == 0

    def getkey(self, ix):
        return self.entries[ix].key

    def find(self, key):
        """
        Searches pages for key, returns relation to key:

        recurse -> found a next level index page to search for key.
                   also returns the next level page nr
        gt -> found a value with a key greater than the one searched for.
        lt -> found a value with a key less than the one searched for.
        eq -> found a value with a key equal to the one searched for.
                   gt, lt and eq return the index for the key found.

        # for an index entry: the key is 'less' than anything in the page pointed to.
        """
        i = binary_search(self.entries, key)
        if i < 0:
            if self.isindex():
                return 'recurse', -1
            return 'gt', 0
        if self.entries[i].key == key:
            return 'eq', i
        if self.isindex():
            return 'recurse', i
        return 'lt', i

    def getpage(self, ix):
        """ For Indexpages, returns the page ptr for the specified entry """
        return self.preceding if ix < 0 else self.entries[ix].npage

    def getval(self, ix):
        """ For all page types, returns the value for the specified entry """
        return self.entries[ix].val

    def rebuild(self, ix, newval):
        total_expand = 0
        for ent in sorted(self.entries, key=lambda e: e.recofs, reverse=True):
            # walk page entries data backwards and move left if expanded
            if ent.i == ix:
                ent.val = newval
                total_expand += len(newval) - ent.vallen
            ent.recofs -= total_expand
        if total_expand > self.free_bytes:
            raise NotImplementedError("no more space in this page")

        bw = BytesWriter(self.br.data)
        bw.writes("LH", self.preceding, self.entrycount)

        for ent in self.entries:
            ent.write_head(bw)

        self.datastart -= total_expand
        bw.writes("LH", self.unk, self.datastart)
        for ent in self.entries:
            ent.write_data(bw)

        self.fh.fo.seek(self.offset)
        self.fh.fo.write(bw.data)


class Cursor:
    """
    A Cursor object represents a position in the b-tree.

    It has methods for moving to the next or previous item.
    And methods for retrieving the key and value of the current position

    The position is represented as a list of (page, index) tuples
    """

    def __init__(self, db, stack):
        self.db = db
        self.stack = stack

    def next(self):
        """ move cursor to next entry """
        page, ix = self.stack.pop()
        if page.isleaf():
            # from leaf move towards root
            ix += 1
            while self.stack and ix == len(page.entries):
                page, ix = self.stack.pop()
                ix += 1
            if ix < len(page.entries):
                self.stack.append((page, ix))
        else:
            # from node move towards leaf
            self.stack.append((page, ix))
            page = self.db.readpage(page.getpage(ix))
            while page.isindex():
                ix = -1
                self.stack.append((page, ix))
                page = self.db.readpage(page.getpage(ix))
            ix = 0
            self.stack.append((page, ix))

    def prev(self):
        """ move cursor to the previous entry """
        page, ix = self.stack.pop()
        ix -= 1
        if page.isleaf():
            # move towards root, until non 'prec' item found
            while self.stack and ix < 0:
                page, ix = self.stack.pop()
            if ix >= 0:
                self.stack.append((page, ix))
        else:
            # move towards leaf
            self.stack.append((page, ix))
            while page.isindex():
                page = self.db.readpage(page.getpage(ix))
                ix = len(page.index) - 1
                self.stack.append((page, ix))

    def eof(self):
        return len(self.stack) == 0

    def getkey(self):
        """ return the key value pointed to by the cursor """
        page, ix = self.stack[-1]
        return page.getkey(ix)

    def getval(self):
        """ return the data value pointed to by the cursor """
        page, ix = self.stack[-1]
        return page.getval(ix)

    def getpageix(self):
        """ return the page and its entry pointed to by the cursor"""
        page, ix = self.stack[-1]
        return page, ix

    def __repr__(self):
        return "cursor:" + repr(self.stack)

class ID0:
    def __init__(self, fh, args):
        self.fh = fh
        compressed, sectlen = self.fh.reads("BQ")
        self.start = fh.tell()

        btreedata = self.fh.read(64)
        self.firstfree, self.pagesize, self.firstindex, \
            self.reccount, self.pagecount = unpack("LHLLL", btreedata)
        if not btreedata[19:].startswith(b"B-tree v2"):
            raise NotImplementedError("unknown b-tree format")

        self.fh.read(self.pagesize - 64)  # rest of btree info
        self.fh.read(self.pagesize)  # page left intentionally blank

        # print(f'reading id0: {self.pagecount} pages')
        #
        # pages = []
        # for i in range(self.pagecount):
        #     print('reading page', i)
        #     pages.append(self.readpage(i))

    def readpage(self, nr):
        self.fh.seek(self.start + nr * self.pagesize)
        return Page(self.fh, self.pagesize)

    def namekey(self, name):
        if type(name) == int:
            return struct.pack("sBQ", b'N', 0, name)
        return b'N' + name.encode('utf-8')

    def nodeByName(self, name):
        cur = self.find('eq', self.namekey(name))
        if cur:
            return struct.unpack('Q', cur.getval())[0]

    def find(self, request, key):
        # descend tree to leaf nearest to the `key`
        page = self.readpage(self.firstindex)
        stack = []
        while True:
            response, ix = page.find(key)
            stack.append((page, ix))
            if len(stack) == 256:
                raise Exception("b-tree corrupted")
            if response != 'recurse':
                break
            page = self.readpage(page.getpage(ix))

        cursor = Cursor(self, stack)

        # now correct for what was actually asked.
        if response == request:
            pass
        elif request == 'eq' and response != 'eq':
            return None
        elif request in ('ge', 'le') and response == 'eq':
            pass
        elif request in ('gt', 'ge') and response == 'lt':
            cursor.next()
        elif request == 'gt' and response == 'eq':
            cursor.next()
        elif request in ('lt', 'le') and response == 'gt':
            cursor.prev()
        elif request == 'lt' and response == 'eq':
            cursor.prev()

        return cursor

    def blob(self, nodeid, tag, start=0, end=0xFFFFFFFF):
        """ returns combined data between multiple entries and all affected pages"""

        def makekey(nodeid, tag, start):
            return struct.pack('>sQsQ', b'.', nodeid, tag.encode('utf-8'), start)

        startkey = makekey(nodeid, tag, start)
        endkey = makekey(nodeid, tag, end)
        cur = self.find('ge', startkey)
        data = b''
        pages = set()
        while cur.getkey() <= endkey:
            page, ix = cur.getpageix()
            pages.add((page, ix))
            chunk = page.entries[ix].val
            data += chunk
            cur.next()
        return data, pages

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


def processfile(args):
    fh = FileHandler(args.srcfile, args.destfile)
    idb = IDBFile(fh)
    id0 = ID0(idb.fh, args)

    if args.list:
        fdl = FuncDirList(id0)
        fdl.print()

    if args.rename:
        fdl = FuncDirList(id0)
        fdl.rename(args)

    fh.close()


class FuncDirList:
    def __init__(self, id0):
        rootnode = id0.nodeByName('$ dirtree/funcs')
        if not rootnode:
            raise ValueError('no function tree entry')

        overview, self.ov_affected = id0.blob(rootnode, 'B', 0, 0xFFFF)
        self.first_dir, self.afterlast_dir = struct.unpack("BB", overview[:2])

        # TODO: decypher
        # sorted_raw = overview[2:]
        # sorted_len = len(sorted_raw)
        # sorted_dirs = struct.unpack(f"{sorted_len}B", sorted_raw)
        # sorted_dirs = [v for v in sorted_dirs]
        # print(sorted_dirs)

        self.dirs = []
        i = self.first_dir
        while True:
            start = i * 0x10000
            end = start + 0xFFFF
            data, affected = id0.blob(rootnode, 'S', start, end)
            if data == b'':
                break
            self.dirs.append(FuncDir(i, data, affected))
            i += 1
        if i > self.afterlast_dir:
            raise Exception("directory count mismatch")

    def print(self):
        for d in self.dirs:
            d.print()

    def rename(self, args):
        for d in self.dirs:
            d.rename(args)


class FuncDir:
    def __init__(self, i, data, affected):
        self.i = i
        self.modified = False
        self.affected = affected
        terminate = data.find(b'\0', 1)
        self.name = data[1:terminate].decode('utf-8')

        p = IdaUnpacker(data[terminate+1:])
        self.parent = p.next64()
        self.unk32 = p.next32()
        subdir_count = p.next32()

        self.subdirs = []
        while subdir_count:
            subdir_id = p.next64signed()
            if self.subdirs:
                subdir_id = self.subdirs[-1] + subdir_id
            self.subdirs.append(subdir_id)
            subdir_count -= 1

        func_count = p.next32()
        self.funcs = []
        while func_count:
            func_id = p.next64signed()
            if self.funcs:
                func_id = self.funcs[-1] + func_id
            self.funcs.append(func_id)
            func_count -= 1

        if not p.eof():
            raise Exception('not EOF after dir parsed')


    def print(self):
        print("dir %d = %s" % (self.i, self.name))
        print(" parent = %d" % self.parent)
        print(" subdirs:")
        for subdir in self.subdirs:
            print("  %d" % subdir)
        # print("  functions:")
        # for func in funcs:
        #     print("  ", end="")
        #     name = id0.name(func)
        #     if name:
        #         print("%x %s" % (func, name))

    def rename(self, args):
        newname = self.name.replace(*args.rename)
        if newname != self.name:
            self.modified = True
            self.name = newname
            self.save()

    def pack(self):
        name = b'\x00' + self.name.encode('utf-8') + b'\x00'
        p = IdaPacker()
        p.push64(self.parent)
        p.push32(self.unk32)
        p.push32(len(self.subdirs))

        if len(self.subdirs):
            baseid = self.subdirs[0]
            p.push64(baseid)
            for subdir in self.subdirs[1:]:
                relative = subdir - baseid
                baseid = subdir
                p.push64signed(relative)

        p.push32(len(self.funcs))

        if len(self.funcs):
            baseofs = self.funcs[0]
            p.push64(baseofs)
            for func in self.funcs[1:]:
                relative = func - baseofs
                baseofs = func
                p.push64signed(relative)

        newdata = name + p.data
        return newdata


    def save(self):
        print('saving FuncDir', self.i)
        if len(self.affected) > 1:
            raise NotImplementedError("dir data Blob spans across multiple Entries")
        for page, ix in self.affected:
            print(f'  affected page {page} entry {ix}')
            newdata = self.pack()
            page.rebuild(ix, newdata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(epilog="""
Makes a copy of .i64 file with certain modifications.

Examples:

  i64edit in.i64 out.i64 --rename FolderNameBad FolderNameGood
""")
    parser.add_argument("srcfile")
    parser.add_argument("destfile")
    parser.add_argument('--list', action='store_true', help='print folder names')
    parser.add_argument('--rename', nargs=2, help='search and replace in folder names')
    args = parser.parse_args()

    copyfile(args.srcfile, args.destfile)
    processfile(args)