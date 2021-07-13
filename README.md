This tool is supposed to fix IDA 7.5 files saved with inconsistent function folder tree. In IDA this leads to empty Functions view when you tick Show folders, but otherwise no error is given.

Based on https://github.com/nlitsme/pyidbutil but with write capability.

### Usage

Check what's wrong:

```
python i64edit.py bad.i64 --check

dir 7 has subdir 147 but 147 is not in tree
check complete
```

Add a new folder where appropriate:

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

Now IDA shows the folder tree correctly. You'll see a CRC warning but that's expected.

### TODO

✅ read folders

✅ modify folder name

✅ move folder to another parent

✅ add new folder

✅ compressed file support

❌ recompute crc32 if modified

❌ resolve issues automagically

❌ in case lots of dirs added at once, might need to add B-tree pages

