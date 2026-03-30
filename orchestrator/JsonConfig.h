#pragma once

#include "Orchestrator.h"
#include <string>

// Parse a JSON config file/string into OrchestratorConfig.
OrchestratorConfig parseConfigFile(const std::string& path);
OrchestratorConfig parseConfigString(const std::string& json_str);
