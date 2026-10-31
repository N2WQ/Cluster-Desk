"""Opt-in CSV spot recording with a bounded asynchronous write queue."""
import csv
import io
from pathlib import Path
import json
import queue
import threading
from datetime import datetime, timezone


MAX_ROW_CHARS = 65536
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RECORDS = 100000

HEADER = ('received_utc', 'host', 'port', 'login', 'date', 'time_utc',
          'frequency_khz', 'dx_call', 'spotter', 'comment', 'dx_dxcc', 'spotter_dxcc',
          'origin', 'dx_itu', 'dx_cq', 'spotter_itu', 'spotter_cq', 'dx_state',
          'spotter_state', 'dx_country', 'spotter_country', 'dx_locator', 'spotter_locator', 'extra_fields_json', 'metadata_fields_json')


class SpotLog:
    def __init__(self, path, capacity=4096, append=False):
        # New recordings use exclusive creation; append opens an existing file only.
        self.base_path=Path(path);self.current_path=str(self.base_path);self.next_part=2;self.record_count=0
        self.file = open(path, 'r+' if append else 'x', encoding='utf-8', newline='')
        try:
            if append:
                self.file.seek(0, 2); size = self.file.tell()
                if size == 0 or size > MAX_FILE_BYTES: raise ValueError('Recording is empty or exceeds the 20 MB limit.')
                self.file.seek(size-1)
                if self.file.read(1) != '\n': raise ValueError('Recording ends with an incomplete line.')
                self.file.seek(0); reader = csv.reader(self.file, strict=True)
                if tuple(next(reader, ())) != HEADER: raise ValueError('Recording has an unsupported header.')
                for count, row in enumerate(reader, 1):
                    if count > MAX_RECORDS or len(row) != len(HEADER) or sum(map(len, row)) > MAX_ROW_CHARS:
                        raise ValueError('Recording contains an invalid or oversized record.')
                    self.record_count=count
                self.file.seek(0, 2)
            self.writer = csv.writer(self.file)
            if not append:
                self.writer.writerow(HEADER)
                self.file.flush()
            self.current_bytes=self.file.tell()
        except Exception:
            self.file.close()
            raise
        self.queue = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.stopping = False
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def publish(self, spot, source):
        with self.lock:
            if self.stopping: return False
            host, port, call = source or ('', '', '')
            row = (datetime.now(timezone.utc).isoformat(), host, port, call,
                   spot.date, spot.time, spot.frequency, spot.dx, spot.spotter, spot.comment,
                   spot.dx_dxcc, spot.spotter_dxcc, spot.origin, spot.dx_itu, spot.dx_cq,
                   spot.spotter_itu, spot.spotter_cq, spot.dx_state, spot.spotter_state,
                   spot.dx_country, spot.spotter_country, spot.dx_locator, spot.spotter_locator,
                   json.dumps(spot.extra_fields, ensure_ascii=False), json.dumps(spot.metadata_fields, ensure_ascii=False))
            if sum(len(str(value)) for value in row) > MAX_ROW_CHARS:
                self.error = 'Recording stopped: a spot record exceeds the supported size and was not recorded.'
                self.stopping = True
                return False
            try: self.queue.put_nowait(row)
            except queue.Full:
                self.error = 'Recording stopped: the disk writer could not keep up; some spots were not recorded.'
                self.stopping = True
                return False
            return True

    def stop(self):
        with self.lock: self.stopping = True

    def _rotate(self):
        self.file.close()
        while self.next_part <= 99999:
            path=self.base_path.with_name(f'{self.base_path.stem}.part-{self.next_part:04d}{self.base_path.suffix}')
            self.next_part+=1
            try:self.file=open(path,'x',encoding='utf-8',newline='')
            except FileExistsError:continue
            self.writer=csv.writer(self.file);self.writer.writerow(HEADER);self.file.flush()
            self.current_bytes=self.file.tell();self.record_count=0
            with self.lock:self.current_path=str(path)
            return
        raise OSError('No unused recording continuation filename is available')

    def _run(self):
        try:
            while True:
                with self.lock: done = self.stopping and self.queue.empty()
                if done: break
                try: row = self.queue.get(timeout=.1)
                except queue.Empty: continue
                buffer=io.StringIO(newline='');csv.writer(buffer).writerow(row)
                row_bytes=len(buffer.getvalue().encode('utf-8'))
                if self.record_count>=MAX_RECORDS or self.current_bytes+row_bytes>MAX_FILE_BYTES:self._rotate()
                if self.current_bytes+row_bytes>MAX_FILE_BYTES:raise OSError('Spot record exceeds the recording file limit')
                self.writer.writerow(row)
                self.file.flush();self.current_bytes+=row_bytes;self.record_count+=1
        except (OSError, UnicodeError, csv.Error) as exc:
            with self.lock:
                self.error = f'Recording stopped: {exc}. Some spots may not have been saved.'
                self.stopping = True
        finally:
            try: self.file.close()
            except OSError as exc:
                with self.lock: self.error = f'Recording could not be finalized: {exc}.'
