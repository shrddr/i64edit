This tool is supposed to fix IDA 7.5 files saved with inconsistent function folder tree. In IDA this leads to empty Functions view when you tick Show folders, but otherwise no error is given.

Based on https://github.com/nlitsme/pyidbutil but with write capability.

### Usage/A

If IDA is not running and you only have a damaged file on disk. First, check what's wrong (this is a readonly operation):

```
python i64edit.py bad.i64 --check

dir 7 has subdir 147 but 147 is not in tree
check complete
```

Start fixing the file by creating a new empty dir#147 with dir#7 as parent:

(`--copyfrom` means keep the original file intact and work with a copy)

```
python i64edit.py --copyfrom bad.i64 good.i64 --insert 147 7

funcdir 147 data empty
applying inserted FuncDir 147
  affected page 47948 entry 20
applying overview
  affected page 47318 entry 129
saving target file...
saving page 47948
saving page 47318
deflating...
moving sections...
```

Open `good.i64` in IDA and it should now display the folder tree. There will be a CRC mismatch warning but that's expected. 

The original contents of dir#147 will be lost and the functions it contained will now be orphaned.

Orphaned functions have no parent dir, and are only shown in list view but not in folder view.

If you happen to know the addresses of orphaned funcs, fix by `--movefunc`, for example add func at 14003BD10 to dir#147:

```
python i64edit.py good.i64 --movefunc 14003BD10 147
```

### Usage/B
If you still have the project open in IDA and it looks alright, but is being saved incorrectly:

```
python i64edit.py online.i64 --check

dir 7 has subdir 147 but 147 is not in tree
check complete

python i64edit.py online.i64 --show 7

dir 7 = filehandling
 parent = 0
 subdirs:
  146 read
  147 ???
  148 unpack
```

There is no name assotiated with dir#147, but you can examine dir#7 'filehandling' in IDA and try to guess which one of its children is dir#147.

Delete and recreate dir#147 using IDA, and the project should now save correctly. Run `--check` once again to be sure (there might be additional issues).

Since this is less damaging then the method above, I always run `--check` before closing IDA, to fix the potential problems in place.

### TODO

✅ read folders

✅ modify folder name

✅ move folder to another parent

✅ add new folder

✅ compressed file support

❌ find orphaned functions

❌ recompute crc32 if modified

❌ resolve issues automagically

❌ in case lots of dirs added at once, might need to add B-tree pages

