# ДЗ-2. Сервис описания изображений

Нужны Bash, работающий Docker Engine в режиме Linux-контейнеров, доступ к
реестрам образов, PyPI, индексу PyTorch CPU и Hugging Face, а также свободные порты
8000 и 50051. Для скачивания весов требуется около 518 МБ. Все команды ниже
выполняются из каталога `homework/02-model-service/starter` в чистом клоне с решением.
Каталог `assets` находится рядом со `starter`.

При запуске из Git Bash на Windows убедитесь, что `docker.exe` есть в `PATH`.
Следующие настройки передают Docker путь к каталогу в формате Windows и
сохраняют пути внутри Linux-контейнера:

```bash
export MSYS2_ARG_CONV_EXCL='*'
pwd() { builtin pwd -W; }
export -f pwd
```

## Модель и тесты

```bash
bash run_tools.sh python download_model.py
bash run_tools.sh pytest -q tests/test_given.py
bash run_tools.sh python -m grpc_tools.protoc -Iproto \
  --python_out=. --grpc_python_out=. proto/predict.proto
bash run_tools.sh pytest -q
```

`run_tools.sh` создаёт сеть `hw2-network` и использует зафиксированные зависимости.
`download_model.py` проверяет SHA-256 файлов модели по манифесту.

## Контейнеры и проверка

```bash
docker build -t hw2:model .
docker volume create hw2-logs
assets_dir=$(cd ../assets && pwd)

start_http() {
  docker run -d --name hw2-http --network hw2-network -p 127.0.0.1:8000:8000 \
    --mount type=bind,source="$assets_dir",target=/models,readonly \
    --mount type=volume,source=hw2-logs,target=/logs hw2:model http
}
start_http
docker run -d --name hw2-grpc --network hw2-network -p 127.0.0.1:50051:50051 \
  --mount type=bind,source="$assets_dir",target=/models,readonly \
  --mount type=volume,source=hw2-logs,target=/logs hw2:model grpc

curl -fsS http://127.0.0.1:8000/health
docker logs hw2-grpc
```

Дождитесь `"ready": true` у HTTP и строки `gRPC ready` в журнале gRPC.
Затем запустите официальный клиент.

```bash
bash run_tools.sh python check.py \
  --http http://hw2-http:8000 --grpc hw2-grpc:50051 --report state/responses.json
```

Клиент сверяет подпись, версию модели и SHA-256 для четырёх примеров во всех
трёх режимах. Он также проверяет уникальность восьми сетевых ID и ошибки входа.
Ожидаемая последняя строка:

```text
OK: 4 captions × Python/HTTP/gRPC; 6 shared invalid inputs + invalid HTTP base64
```

Следующая команда должна завершиться ошибкой `Read-only file system`.

```bash
docker exec hw2-http python -c \
  'from pathlib import Path; Path("/models/write-probe").write_text("x")'
```

Извлеките gRPC ID из отчёта и найдите одну и ту же запись в журнале до и после
пересоздания HTTP-контейнера. Команды удаления ниже относятся к контейнерам,
запущенным в этом сценарии.

```bash
grpc_id=$(bash run_tools.sh python -c \
  'import json; from pathlib import Path; print(next(r["request_id"] for r in json.loads(Path("state/responses.json").read_text()) if r["protocol"] == "grpc"))')
docker exec hw2-http grep -F "$grpc_id" /logs/requests.jsonl
docker rm -f hw2-http hw2-grpc
start_http
docker exec hw2-http grep -F "$grpc_id" /logs/requests.jsonl
docker rm -f hw2-http
```

Наконец, проверьте отказ запуска. Ожидаются ненулевой код выхода и сообщение
`Model files missing`.

```bash
empty_dir=$(mktemp -d)
docker run --rm --mount type=bind,source="$(cd "$empty_dir" && pwd)",target=/models,readonly hw2:model http
rmdir "$empty_dir"
```

Bind mount подключает один проверенный комплект весов с хоста в режиме чтения.
Общий именованный том `hw2-logs` даёт обоим процессам доступ к
`/logs/requests.jsonl` и хранит файл независимо от срока жизни контейнеров.

## Контракт

| Интерфейс | Запрос | Ошибка ввода |
| --- | --- | --- |
| HTTP `GET /health` | Без тела. Возвращает `ready` и `model_version`. | — |
| HTTP `POST /predict` | JSON со строкой `image_base64` и необязательным целым `max_new_tokens`. | HTTP 422 |
| gRPC `course.Predictor/Predict` | `CaptionRequest` с байтами файла в `image` и необязательным `max_new_tokens`. | `INVALID_ARGUMENT` |

Оба транспорта передают исходные байты в `CaptionInput` и вызывают
`ModelRunner.predict`. Каждый процесс загружает модель при старте и возвращает
`caption`, `model_version`, `request_id` и `image_sha256`. Принимаются JPEG и
PNG до 1 048 576 байт и 1 048 576 пикселей включительно. Допустимый лимит
генерации — от 1 до 48. Пропущенный лимит передаётся в модель как `None`,
после чего модель использует 24. Явный 0 отклоняется. В gRPC это различие
сохраняется за счёт `optional` и `HasField`.

## Дополнительные тесты

- PNG размером 1024 × 1024 успешно проходит HTTP-валидацию на границе
  пиксельного лимита. Тест сверяет исходные байты и SHA-256 ответа.
- Перевод строки внутри base64 корректного JPEG даёт HTTP 422 до инференса.
  Тест ловит декодирование в нестрогом режиме.

## Результаты проверки

### Docker Desktop

Docker Desktop 4.99.0, Engine 29.9.3, `linux/amd64`. Образ `hw2:model`
собран на `python:3.13.12-slim`. В образе проверено наличие обоих
сгенерированных модулей Protobuf и отсутствие каталогов `/models` и `/logs`.
Оба сервиса использовали этот образ в сети `hw2-network`. Проверка
`docker inspect` подтвердила у обоих bind mount `/models` только для чтения
и общий том `hw2-logs` с записью в `/logs`.

```text
{"ready":true,"model_version":"smolvlm-256m-7e3e67edbbed-caption-v1"}
gRPC ready model_version=smolvlm-256m-7e3e67edbbed-caption-v1
```

Генерация Protobuf и полный набор тестов выполнены через `run_tools.sh`.
Вывод pytest:

```text
28 passed, 1 warning in 17.43s
```

Официальный `check.py` выполнен в контейнере `run_tools.sh` через
`http://hw2-http:8000` и `hw2-grpc:50051`. Подпись, версия модели и SHA-256
совпали во всех трёх режимах для каждого из четырёх примеров. В
`state/responses-docker.json` записаны четыре HTTP-ответа и четыре gRPC-ответа
с восемью уникальными ID. Вывод клиента:

```text
cats-default: In this image we can see two cats on the couch.
bee-24: In this image there are flowers and plants.
candy-24: In this image we can see a hand with some objects on it.
cats-one-token: In
OK: 4 captions × Python/HTTP/gRPC; 6 shared invalid inputs + invalid HTTP base64
```

Запись в `/models` завершилась ошибкой:

```text
OSError: [Errno 30] Read-only file system: '/models/write-probe'
```

Из HTTP-контейнера прочитана запись gRPC с ID
`6bb34b34d6784076b3780a742c1cc8bb`. После удаления обоих контейнеров
и запуска нового HTTP-контейнера с тем же томом строка совпала целиком.

```json
{"time": 1795379435.1565876, "protocol": "grpc", "max_new_tokens": 24, "caption": "In this image we can see two cats on the couch.", "model_version": "smolvlm-256m-7e3e67edbbed-caption-v1", "request_id": "6bb34b34d6784076b3780a742c1cc8bb", "image_sha256": "03e3947df1ec190bbcc1f3cf47e56a5b60f9415be1ba072d1837d49380538b5c"}
```

HTTP-контейнер с пустым каталогом, подключённым к `/models`, завершился
с кодом 3 и сообщением:

```text
FileNotFoundError: Model files missing in /models
```

### Предварительный прогон на Windows

На Windows зависимости установлены через `uv sync --locked` с Python 3.13.12.
Скачивание завершилось сообщением `Model download OK; all SHA-256 match`.
Классы Protobuf сгенерированы локально командой из раздела «Модель и тесты»
через `uv run --locked`.

```text
tests/test_given.py   9 passed in 27.07s
tests/              28 passed, 1 warning in 7.87s
```

Для Windows применён временный запускатель pytest, заменяющий длинные ID
параметров типа `bytes` на короткие. Без него pytest пытается записать ID
мегабайтного параметра в переменную `PYTEST_CURRENT_TEST` и превышает лимит
Windows в 32 767 символов. Входы, выданные тесты и их проверки сохранены.
Предупреждение pytest относится к использованию `httpx` в Starlette TestClient.

Официальный `check.py` выполнен с двумя локальными процессами
`python service.py http` и `python service.py grpc` на портах 8000 и 50051.
Для обхода системного прокси использована переменная
`NO_PROXY=127.0.0.1,localhost`. Оба процесса подтвердили готовность с версией
`smolvlm-256m-7e3e67edbbed-caption-v1`. Клиент сохранил восемь сетевых ответов
в `state/responses.json` и завершился строкой:

```text
OK: 4 captions × Python/HTTP/gRPC; 6 shared invalid inputs + invalid HTTP base64
```

Запись gRPC с `request_id=4e6b36ad013e452d939ed9e0533872d6` найдена в общем
локальном файле `state/requests.jsonl` до остановки обоих процессов и после
повторного запуска HTTP. С пустым локальным каталогом модели HTTP завершился
с кодом 3, gRPC с кодом 1, оба с сообщением `FileNotFoundError: Model files missing`.
Дополнительная проверка подтвердила, что PNG с повреждённой контрольной суммой
даёт HTTP 422 и gRPC `INVALID_ARGUMENT`. Искусственная ошибка инференса дала
HTTP 500 и gRPC `UNKNOWN`.

Реализация и запуск этих проверок выполнены с помощью Codex.
