#!/bin/bash
# Coupe les ouvriers si la mémoire libre du Spark passe sous 5 Go (le chef et le Correcteur restent).
while true; do
  dispo=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  if [ "$dispo" -lt 5 ] && docker ps -q -f name=^ouvriers$ | grep -q .; then
    echo "$(date '+%F %T') GARDE : $dispo Go libres, ouvriers arrêtés"
    systemctl stop vllm-ouvriers.service
  fi
  sleep 3
done
