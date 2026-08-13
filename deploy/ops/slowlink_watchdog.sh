#!/bin/sh
set -eu

APP_CONTAINER="${APP_CONTAINER:-slowlink_app}"
REDIS_CONTAINER="${REDIS_CONTAINER:-slowlink_redis}"
CHECK_INTERVAL="${CHECK_INTERVAL:-10}"
CPU_THRESHOLD="${CPU_THRESHOLD:-80}"
HIGH_COUNT_LIMIT="${HIGH_COUNT_LIMIT:-4}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-600}"
LOG_FILE="${LOG_FILE:-/opt/slowlink/watchdog.log}"
STACK_DUMP_PATH="${STACK_DUMP_PATH:-/tmp/slowlink_python_stack.log}"
STACK_SAMPLE_COUNT="${STACK_SAMPLE_COUNT:-3}"
STACK_SAMPLE_INTERVAL="${STACK_SAMPLE_INTERVAL:-1}"

high_count=0
last_restart=0
last_cpu_usage=0
last_cpu_time=0
last_cpu_path=""
cpu=""

log() {
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  line="[$ts] $*"
  echo "$line"
  printf '%s\n' "$line" >> "$LOG_FILE" 2>/dev/null || true
}

sample_container_cpu() {
  cpu=""
  pid="$(docker inspect -f '{{.State.Pid}}' "$APP_CONTAINER" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*|0) return 1 ;;
  esac

  cgroup_rel="$(awk -F: '$1 == "0" {print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null || true)"
  usage_file="/sys/fs/cgroup${cgroup_rel}/cpu.stat"
  if [ -z "$cgroup_rel" ] || [ ! -r "$usage_file" ]; then
    cpu="$(docker stats --no-stream --format '{{.CPUPerc}}' "$APP_CONTAINER" 2>/dev/null | tr -d '%' | awk '{printf "%.0f", $1}')"
    [ -n "$cpu" ]
    return
  fi

  usage="$(awk '$1 == "usage_usec" {print $2; exit}' "$usage_file" 2>/dev/null || true)"
  now="$(date +%s)"
  case "$usage:$now" in
    *[!0-9:]*|:*) return 1 ;;
  esac

  if [ "$usage_file" != "$last_cpu_path" ] || [ "$last_cpu_usage" -eq 0 ] || [ "$usage" -lt "$last_cpu_usage" ]; then
    last_cpu_path="$usage_file"
    last_cpu_usage="$usage"
    last_cpu_time="$now"
    cpu=0
    return 0
  fi

  elapsed=$((now - last_cpu_time))
  if [ "$elapsed" -le 0 ]; then
    return 1
  fi
  delta=$((usage - last_cpu_usage))
  denominator=$((elapsed * 10000))
  cpu=$(((delta + denominator / 2) / denominator))
  last_cpu_usage="$usage"
  last_cpu_time="$now"
}

write_thread_ticks() {
  thread_pid=$1
  output_file=$2
  : > "$output_file"
  for stat_file in /proc/"$thread_pid"/task/*/stat; do
    [ -r "$stat_file" ] || continue
    stat_line="$(cat "$stat_file" 2>/dev/null || true)"
    [ -n "$stat_line" ] || continue
    tid=${stat_line%% *}
    stat_fields=${stat_line##*) }
    set -- $stat_fields
    ticks=$((${12} + ${13}))
    printf '%s %s\n' "$tid" "$ticks" >> "$output_file"
  done
}

capture_python_state() {
  reason=$1
  log "python stack series requested: $reason (${STACK_SAMPLE_COUNT} samples)"
  thread_pid="$(docker inspect -f '{{.State.Pid}}' "$APP_CONTAINER" 2>/dev/null || true)"
  thread_before="$(mktemp /tmp/slowlink-thread-before.XXXXXX)"
  thread_after="$(mktemp /tmp/slowlink-thread-after.XXXXXX)"
  thread_started="$(date +%s)"
  case "$thread_pid" in
    ''|*[!0-9]*|0) : > "$thread_before" ;;
    *) write_thread_ticks "$thread_pid" "$thread_before" ;;
  esac

  docker exec "$APP_CONTAINER" sh -c ': > "$1"' sh "$STACK_DUMP_PATH" >/dev/null 2>&1 || true
  sample=1
  stack_ok=0
  while [ "$sample" -le "$STACK_SAMPLE_COUNT" ]; do
    if docker exec "$APP_CONTAINER" sh -c 'kill -USR1 1' >/dev/null 2>&1; then
      stack_ok=1
    fi
    sleep "$STACK_SAMPLE_INTERVAL"
    sample=$((sample + 1))
  done

  if [ "$stack_ok" -eq 1 ]; then
    {
      printf '\n===== Python stack series: %s =====\n' "$reason"
      docker exec "$APP_CONTAINER" sh -c 'if [ -s "$1" ]; then cat "$1"; : > "$1"; else echo "stack unavailable"; fi' sh "$STACK_DUMP_PATH"
    } >> "$LOG_FILE" 2>&1
    log "python stack series saved to $LOG_FILE"
  else
    log "python stack request failed"
  fi

  thread_ended="$(date +%s)"
  thread_elapsed=$((thread_ended - thread_started))
  [ "$thread_elapsed" -gt 0 ] || thread_elapsed=1
  case "$thread_pid" in
    ''|*[!0-9]*|0) : > "$thread_after" ;;
    *) write_thread_ticks "$thread_pid" "$thread_after" ;;
  esac
  {
    printf '===== thread CPU window: %ss =====\n' "$thread_elapsed"
    ticks_per_second="$(getconf CLK_TCK 2>/dev/null || printf '100')"
    awk -v hz="$ticks_per_second" -v elapsed="$thread_elapsed" '
      NR == FNR { before[$1] = $2; next }
      $1 in before {
        delta = $2 - before[$1]
        if (delta < 0) delta = 0
        printf "%s %.1f %d\n", $1, delta * 100 / hz / elapsed, delta
      }
    ' "$thread_before" "$thread_after" \
      | sort -k2,2nr \
      | awk '{printf "  tid=%s cpu=%s%% ticks=%s\n", $1, $2, $3}'
  } >> "$LOG_FILE" 2>&1
  rm -f "$thread_before" "$thread_after"

  flow="$(docker exec "$REDIS_CONTAINER" redis-cli --raw GET listener_flow_stats 2>/dev/null || true)"
  log "listener flow: ${flow:-unavailable}"
}

snapshot() {
  log "snapshot: load=$(cut -d ' ' -f1-3 /proc/loadavg 2>/dev/null || true)"
  docker stats --no-stream "$APP_CONTAINER" "$REDIS_CONTAINER" >> "$LOG_FILE" 2>/dev/null || true
  ps -eo pid,tid,ppid,comm,%cpu,%mem,etime --sort=-%cpu | head -20 >> "$LOG_FILE" 2>/dev/null || true
}

log "watchdog started: container=$APP_CONTAINER threshold=${CPU_THRESHOLD}% count=$HIGH_COUNT_LIMIT interval=${CHECK_INTERVAL}s cooldown=${COOLDOWN_SECONDS}s"

while true; do
  sample_container_cpu || true
  case "$cpu" in
    ''|*[!0-9]*)
      high_count=0
      sleep "$CHECK_INTERVAL"
      continue
      ;;
  esac

  if [ "$cpu" -ge "$CPU_THRESHOLD" ]; then
    high_count=$((high_count + 1))
    log "high CPU: ${cpu}% (${high_count}/${HIGH_COUNT_LIMIT})"
    if [ "$high_count" -eq 1 ]; then
      snapshot
      capture_python_state "first high CPU sample"
    fi
  else
    if [ "$high_count" -gt 0 ]; then
      log "CPU recovered: ${cpu}% (reset high count)"
    fi
    high_count=0
  fi

  now="$(date +%s)"
  if [ "$high_count" -ge "$HIGH_COUNT_LIMIT" ]; then
    since=$((now - last_restart))
    if [ "$last_restart" -eq 0 ] || [ "$since" -ge "$COOLDOWN_SECONDS" ]; then
      snapshot
      log "restarting $APP_CONTAINER after sustained high CPU"
      if docker restart "$APP_CONTAINER" >> "$LOG_FILE" 2>&1; then
        log "restart completed for $APP_CONTAINER"
        last_restart="$now"
      else
        log "restart failed for $APP_CONTAINER"
      fi
    else
      log "restart skipped by cooldown: ${since}s/${COOLDOWN_SECONDS}s"
    fi
    high_count=0
  fi

  sleep "$CHECK_INTERVAL"
done
