#pragma once
// Filesystem shim for RP2040_PLATFORM path.
// ORCHESTRATOR_BUILD: fully in-memory (no disk I/O).
// Otherwise: POSIX-backed via a directory on disk.

#include <Stream.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string>

#ifdef ORCHESTRATOR_BUILD
#include <vector>
#include <memory>
#include <unordered_map>
#endif

struct FSInfo {
    size_t totalBytes;
    size_t usedBytes;
};

// Forward declare
namespace fs { class FS; }

class File : public Stream {
#ifdef ORCHESTRATOR_BUILD
    // In-memory backend: shared_ptr keeps data alive even if FS entry is removed
    std::shared_ptr<std::vector<uint8_t>> _data;
    size_t _pos = 0;
    bool _writable = false;
    std::string _name;

    friend class fs::FS;
    File(std::shared_ptr<std::vector<uint8_t>> data, const std::string& name,
         bool writable, size_t pos = 0)
        : _data(std::move(data)), _pos(pos), _writable(writable), _name(name) {}

public:
    File() = default;

    // Move semantics
    File(File&& o) noexcept
        : _data(std::move(o._data)), _pos(o._pos), _writable(o._writable),
          _name(std::move(o._name)) { o._pos = 0; }
    File& operator=(File&& o) noexcept {
        if (this != &o) {
            _data = std::move(o._data); _pos = o._pos;
            _writable = o._writable; _name = std::move(o._name);
            o._pos = 0;
        }
        return *this;
    }

    // No copy
    File(const File&) = delete;
    File& operator=(const File&) = delete;

    operator bool() const { return _data != nullptr; }

    const char* name() const { return _name.c_str(); }
    const char* fullName() const { return _name.c_str(); }

    size_t size() { return _data ? _data->size() : 0; }
    bool isDirectory() const { return false; }

    size_t write(uint8_t c) override {
        if (!_data || !_writable) return 0;
        if (_pos >= _data->size()) _data->resize(_pos + 1);
        (*_data)[_pos++] = c;
        return 1;
    }
    size_t write(const uint8_t* buf, size_t n) override {
        if (!_data || !_writable || n == 0) return 0;
        if (_pos + n > _data->size()) _data->resize(_pos + n);
        memcpy(_data->data() + _pos, buf, n);
        _pos += n;
        return n;
    }
    int read() override {
        if (!_data || _pos >= _data->size()) return -1;
        return (*_data)[_pos++];
    }
    size_t read(uint8_t* buf, size_t n) {
        if (!_data) return 0;
        size_t avail = _data->size() > _pos ? _data->size() - _pos : 0;
        size_t to_read = n < avail ? n : avail;
        if (to_read > 0) {
            memcpy(buf, _data->data() + _pos, to_read);
            _pos += to_read;
        }
        return to_read;
    }
    int available() override {
        if (!_data) return 0;
        return (int)(_data->size() > _pos ? _data->size() - _pos : 0);
    }
    int peek() override {
        if (!_data || _pos >= _data->size()) return -1;
        return (*_data)[_pos];
    }
    void flush() override {}
    bool seek(uint32_t pos) {
        if (!_data) return false;
        if (pos > _data->size()) return false;
        _pos = pos;
        return true;
    }
    uint32_t position() {
        return (uint32_t)_pos;
    }

    void close() { _data.reset(); _pos = 0; }

    File openNextFile() { return File(); }

#else
    // POSIX backend
    FILE* _fp;
    std::string _name;
    std::string _fullpath;
    bool _is_dir;
    friend class fs::FS;

    File(FILE* fp, const std::string& name, const std::string& fullpath, bool is_dir = false)
        : _fp(fp), _name(name), _fullpath(fullpath), _is_dir(is_dir) {}

public:
    File() : _fp(nullptr), _is_dir(false) {}

    ~File() { close(); }

    // Move semantics
    File(File&& o) noexcept : _fp(o._fp), _name(std::move(o._name)),
        _fullpath(std::move(o._fullpath)), _is_dir(o._is_dir) { o._fp = nullptr; }
    File& operator=(File&& o) noexcept {
        if (this != &o) {
            close();
            _fp = o._fp; _name = std::move(o._name);
            _fullpath = std::move(o._fullpath); _is_dir = o._is_dir;
            o._fp = nullptr;
        }
        return *this;
    }

    // No copy
    File(const File&) = delete;
    File& operator=(const File&) = delete;

    operator bool() const { return _fp != nullptr || _is_dir; }

    const char* name() const { return _name.c_str(); }
    const char* fullName() const { return _fullpath.c_str(); }

    size_t size() {
        if (!_fp) return 0;
        long pos = ftell(_fp);
        fseek(_fp, 0, SEEK_END);
        long sz = ftell(_fp);
        fseek(_fp, pos, SEEK_SET);
        return (size_t)(sz > 0 ? sz : 0);
    }

    bool isDirectory() const { return _is_dir; }

    // Stream interface
    size_t write(uint8_t c) override {
        if (!_fp) return 0;
        return fwrite(&c, 1, 1, _fp);
    }
    size_t write(const uint8_t* buf, size_t n) override {
        if (!_fp) return 0;
        return fwrite(buf, 1, n, _fp);
    }
    int read() override {
        if (!_fp) return -1;
        return fgetc(_fp);
    }
    size_t read(uint8_t* buf, size_t n) {
        if (!_fp) return 0;
        return fread(buf, 1, n, _fp);
    }
    int available() override {
        if (!_fp) return 0;
        long pos = ftell(_fp);
        fseek(_fp, 0, SEEK_END);
        long end = ftell(_fp);
        fseek(_fp, pos, SEEK_SET);
        return (int)(end - pos);
    }
    int peek() override {
        if (!_fp) return -1;
        int c = fgetc(_fp);
        if (c != EOF) ungetc(c, _fp);
        return c;
    }
    void flush() override {
        if (_fp) fflush(_fp);
    }
    bool seek(uint32_t pos) {
        if (!_fp) return false;
        return fseek(_fp, (long)pos, SEEK_SET) == 0;
    }
    uint32_t position() {
        if (!_fp) return 0;
        return (uint32_t)ftell(_fp);
    }

    void close() {
        if (_fp) { fclose(_fp); _fp = nullptr; }
    }

    // Directory iteration (simplified for sim)
    File openNextFile();
#endif
};

namespace fs {

class FS {
    std::string _root;
#ifdef ORCHESTRATOR_BUILD
    std::unordered_map<std::string, std::shared_ptr<std::vector<uint8_t>>> _files;
#endif

    std::string resolvePath(const char* path) const;

public:
    FS();

    // Initialize with a root directory (in-memory: just stores the name)
    bool begin(const char* root_dir = nullptr);

    bool exists(const char* path);
    File open(const char* path, const char* mode = "r");
    bool remove(const char* path);
    bool mkdir(const char* path);
    bool rename(const char* pathFrom, const char* pathTo);
    bool format();
    bool info(FSInfo& info_out);
};

} // namespace fs

// Global LittleFS instance
#ifdef ORCHESTRATOR_BUILD
  extern fs::FS* _ctx_fs;
  #define LittleFS (*_ctx_fs)
#else
  extern fs::FS LittleFS;
#endif
