#!/bin/bash
# infra/rabbitmq-queue-reset.sh
# 迁移用:删除旧队列(无 DLX args),让新代码以带 DLX 的 args 重新声明。
# 旧队列: parsing.urgent parsing.normal interpretation.urgent interpretation.normal dead.letter
set -euo pipefail

RABBIT_CONTAINER="${RABBIT_CONTAINER:-hospital-rabbitmq}"

QUEUES=(parsing.urgent parsing.normal interpretation.urgent interpretation.normal dead.letter)

echo "[INFO] RabbitMQ 队列迁移: 删除旧队列(无 DLX args)..."
echo "[INFO] 容器: $RABBIT_CONTAINER"
echo "[INFO] 待删除队列:"
for q in "${QUEUES[@]}"; do
  echo "  - $q"
done

for q in "${QUEUES[@]}"; do
  echo "[INFO] 删除队列: $q"
  docker exec "$RABBIT_CONTAINER" rabbitmqctl delete_queue "$q" 2>/dev/null || true
done

echo "Old queues deleted; new code will recreate with DLX args."
