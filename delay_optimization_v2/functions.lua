  -- adaptive_delays.lua                                                                                                     
  sim:initialize()
                                                                                                                             
  -- After hot start, classify each repeater and set delays                 
  sim:for_each_repeater(function(name, status)
      local good = 0   -- SNR > 0
      local weak = 0   -- SNR <= 0

      for _, nb in ipairs(status.neighbors or {}) do
          if nb.snr_db > 0 then
              good = good + 1
          else
              weak = weak + 1
          end
      end

      local total = good + weak
      log(string.format("%s: %d neighbors (%d good, %d weak)", name, total, good, weak))

      if total > 5 then
          -- Dense area: longer delays to reduce collisions
          sim:cmd(name, "set txdelay 3.0")
          sim:cmd(name, "set rxdelay 3.0")
      elseif weak > good then
          -- Mostly weak links: shorter TX delay to help reach
          sim:cmd(name, "set txdelay 1.0")
          sim:cmd(name, "set rxdelay 1.0")
      else
          -- Default
          sim:cmd(name, "set txdelay 2.0")
          sim:cmd(name, "set rxdelay 2.0")
      end
  end)

  sim:run_all()
  sim:finalize()

