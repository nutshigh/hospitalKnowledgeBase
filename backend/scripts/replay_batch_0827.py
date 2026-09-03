"""重跑今天的批量上传两份报告: 清理坏数据 → 重投 parse。

用法: .venv/bin/python scripts/replay_batch_0827.py
（需 root 旧 worker 已停, 避免竞争）
"""
import sys
sys.path.insert(0, '.')

import pymysql

BATCH = '9e87e87d3da840099680e3a0b1954c8c'
FILES = [
    ('hospital_H004', 5, '26800bfad856448ba9859bfe6c194f20.pdf', '步新宇_H004_11.pdf'),
    ('hospital_H003', 5, '7419e7ed97b441c68e73b393679d22aa.pdf', '陈美杉_H003_10.pdf'),
]


def main():
    for db_name, task_id, fname, orig in FILES:
        conn = pymysql.connect(host='127.0.0.1', user='root', password='root',
                               port=3306, database=db_name, charset='utf8mb4')
        cur = conn.cursor()
        cur.execute('SELECT id FROM report_interpretation WHERE report_id=%s', (task_id,))
        for (iid,) in cur.fetchall():
            cur.execute('DELETE FROM indicator_judgment WHERE interpretation_id=%s', (iid,))
        cur.execute('DELETE FROM report_interpretation WHERE report_id=%s', (task_id,))
        cur.execute('DELETE FROM report_indicator WHERE report_id=%s', (task_id,))
        cur.execute('UPDATE report_info SET name=NULL, gender=NULL, age=NULL, report_date=NULL, '
                    'unit_name=NULL, conclusion_text=NULL WHERE id=%s', (task_id,))
        cur.execute("UPDATE report_task SET status='queued', error_message=NULL, "
                    "completed_at=NULL WHERE id=%s", (task_id,))
        conn.commit()
        print(f'{db_name} task {task_id}: cleaned (indicators/interp/judgment/conclusion)')
        conn.close()

    # 重投 parse(bulk 优先级)
    from app.core.rabbitmq import RabbitMQClient, TaskMessage
    rq = RabbitMQClient()
    for db_name, task_id, fname, orig in FILES:
        disk = f'./storage/H001/batch/extracted/{BATCH}/{fname}'
        rq.publish(TaskMessage(
            task_type='parsing', hospital_id=db_name.replace('hospital_', ''),
            priority='bulk',
            payload={'task_id': task_id, 'hospital_id': db_name.replace('hospital_', ''),
                     'file_path': disk, 'batch_id': BATCH},
        ))
        print(f'{db_name} task {task_id} republished -> parsing.bulk')


if __name__ == '__main__':
    main()
