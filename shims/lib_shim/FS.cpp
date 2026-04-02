#include "FS.h"

#ifndef ORCHESTRATOR_BUILD
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <dirent.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <vector>
#endif

// Global LittleFS instance
#ifdef ORCHESTRATOR_BUILD
fs::FS* _ctx_fs = nullptr;
#else
fs::FS LittleFS;
#endif

// ---------- File directory iteration ----------
#ifndef ORCHESTRATOR_BUILD
File File::openNextFile() {
    return File();
}
#endif

// ---------- fs::FS implementation ----------

namespace fs {

#ifdef ORCHESTRATOR_BUILD
// ===================== In-memory backend =====================

FS::FS() {}

std::string FS::resolvePath(const char* path) const {
    if (!path) return "";
    // Normalize: strip leading slash for consistent map keys
    if (path[0] == '/') path++;
    return std::string(path);
}

bool FS::begin(const char* root_dir) {
    if (root_dir) _root = root_dir;
    if (_root.empty()) _root = "mem";
    return true;
}

bool FS::exists(const char* path) {
    return _files.count(resolvePath(path)) > 0;
}

File FS::open(const char* path, const char* mode) {
    std::string key = resolvePath(path);
    if (key.empty()) return File();

    // Extract filename for File::name()
    std::string name = key;
    size_t last_slash = name.rfind('/');
    if (last_slash != std::string::npos) name = name.substr(last_slash + 1);

    bool read_mode  = (strcmp(mode, "r") == 0 || strcmp(mode, "rb") == 0);
    bool rw_mode    = (strcmp(mode, "r+") == 0 || strcmp(mode, "r+b") == 0);
    bool write_mode = (strcmp(mode, "w") == 0 || strcmp(mode, "wb") == 0 ||
                       strcmp(mode, "w+") == 0 || strcmp(mode, "w+b") == 0);
    bool append_mode = (strcmp(mode, "a") == 0 || strcmp(mode, "ab") == 0);

    if (read_mode || rw_mode) {
        auto it = _files.find(key);
        if (it == _files.end()) return File();
        return File(it->second, name, rw_mode, 0);
    }

    if (write_mode) {
        // Create or truncate
        auto data = std::make_shared<std::vector<uint8_t>>();
        _files[key] = data;
        return File(data, name, true, 0);
    }

    if (append_mode) {
        auto it = _files.find(key);
        if (it != _files.end()) {
            // Existing: position at end
            return File(it->second, name, true, it->second->size());
        }
        // New
        auto data = std::make_shared<std::vector<uint8_t>>();
        _files[key] = data;
        return File(data, name, true, 0);
    }

    return File();
}

bool FS::remove(const char* path) {
    return _files.erase(resolvePath(path)) > 0;
}

bool FS::mkdir(const char*) {
    // No-op for in-memory FS
    return true;
}

bool FS::rename(const char* pathFrom, const char* pathTo) {
    std::string from = resolvePath(pathFrom);
    std::string to   = resolvePath(pathTo);
    auto it = _files.find(from);
    if (it == _files.end()) return false;
    _files[to] = std::move(it->second);
    _files.erase(it);
    return true;
}

bool FS::format() {
    _files.clear();
    return true;
}

bool FS::info(FSInfo& info_out) {
    size_t used = 0;
    for (const auto& kv : _files) used += kv.second->size();
    info_out.totalBytes = 4 * 1024 * 1024; // 4 MB virtual
    info_out.usedBytes = used;
    return true;
}

#else
// ===================== POSIX backend =====================

FS::FS() {}

std::string FS::resolvePath(const char* path) const {
    if (!path || !path[0]) return _root;
    // Build raw path
    std::string raw = _root;
    if (path[0] == '/') {
        raw += path;
    } else {
        raw += "/";
        raw += path;
    }
    // Normalize: resolve . and .. components, prevent escaping _root
    std::vector<std::string> parts;
    std::string seg;
    for (size_t i = 0; i <= raw.size(); i++) {
        if (i == raw.size() || raw[i] == '/') {
            if (seg == "..") {
                if (!parts.empty()) parts.pop_back();
            } else if (!seg.empty() && seg != ".") {
                parts.push_back(seg);
            }
            seg.clear();
        } else {
            seg += raw[i];
        }
    }
    std::string normalized = "/";
    for (size_t i = 0; i < parts.size(); i++) {
        if (i > 0) normalized += "/";
        normalized += parts[i];
    }
    // Ensure result stays within _root
    if (normalized.find(_root) != 0) return _root;
    return normalized;
}

bool FS::begin(const char* root_dir) {
    if (root_dir) _root = root_dir;
    if (_root.empty()) _root = "/tmp/meshsim_default";
    // Ensure root directory exists
    ::mkdir(_root.c_str(), 0755);
    return true;
}

bool FS::exists(const char* path) {
    std::string full = resolvePath(path);
    struct stat st;
    return stat(full.c_str(), &st) == 0;
}

File FS::open(const char* path, const char* mode) {
    std::string full = resolvePath(path);

    // Translate Arduino-style modes to fopen modes
    const char* fmode = mode;
    if (strcmp(mode, "r") == 0) fmode = "rb";
    else if (strcmp(mode, "w") == 0) fmode = "wb";
    else if (strcmp(mode, "a") == 0) fmode = "ab";
    else if (strcmp(mode, "r+") == 0) fmode = "r+b";
    else if (strcmp(mode, "w+") == 0) fmode = "w+b";

    FILE* fp = fopen(full.c_str(), fmode);
    if (!fp) return File();

    // Extract just the filename
    std::string name = path;
    size_t last_slash = name.rfind('/');
    if (last_slash != std::string::npos) {
        name = name.substr(last_slash + 1);
    }

    return File(fp, name, full);
}

bool FS::remove(const char* path) {
    std::string full = resolvePath(path);
    return ::remove(full.c_str()) == 0;
}

bool FS::mkdir(const char* path) {
    std::string full = resolvePath(path);
    // Create directory recursively
    std::string current;
    for (size_t i = 0; i < full.size(); i++) {
        current += full[i];
        if (full[i] == '/' || i == full.size() - 1) {
            ::mkdir(current.c_str(), 0755);
        }
    }
    return true;
}

bool FS::rename(const char* pathFrom, const char* pathTo) {
    std::string from = resolvePath(pathFrom);
    std::string to = resolvePath(pathTo);
    return ::rename(from.c_str(), to.c_str()) == 0;
}

bool FS::format() {
    // Remove all files in the root directory (simulate format)
    DIR* dir = opendir(_root.c_str());
    if (!dir) return false;
    bool all_ok = true;
    struct dirent* ent;
    while ((ent = readdir(dir)) != nullptr) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        std::string full = _root + "/" + ent->d_name;
        if (::remove(full.c_str()) != 0) all_ok = false;
    }
    closedir(dir);
    return all_ok;
}

bool FS::info(FSInfo& info_out) {
    struct statvfs sv;
    if (statvfs(_root.c_str(), &sv) != 0) return false;
    info_out.totalBytes = sv.f_blocks * sv.f_frsize;
    info_out.usedBytes = (sv.f_blocks - sv.f_bfree) * sv.f_frsize;
    return true;
}

#endif

} // namespace fs
