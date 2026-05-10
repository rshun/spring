"""
同步股本变迁数据 (CAPITAL_DETAIL)

从通达信服务器下载gbbq股本变迁文件，解密后写入数据库。
下载文件存放在项目 download/ 目录，gbbq.csv 读写在 csv/ 目录。

用法:
    python -m etl.sync_capital
    python -m etl.sync_capital --help
"""
import argparse
import duckdb
import logging
import os
import struct
import hashlib
import zipfile
import threading
import time
import pandas as pd
from pathlib import Path
from queue import Queue
from util.myutil import configure_etl_logging
from util.config import get_config

logger = logging.getLogger("etl.sync_capital")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = PROJECT_ROOT / "download"
CSV_DIR      = PROJECT_ROOT / "csv"

GBBQ_FILE    = CSV_DIR / "gbbq"

# 通达信财务文件名格式: "gpcw" + "YYYYMMDD" + ".ext" = 4+8+4 = 16 字符
_CW_FILENAME_LEN = 16


def _get_tdx_config():
    """从 config.yaml 读取通达信财务相关配置"""
    return get_config()["tdx"]


# ── 工具函数 ──────────────────────────────────────────────

def download_url(url):
    import requests

    cfg = _get_tdx_config()["download"]
    tries   = cfg["tries"]
    delay   = cfg["retry_delay"]
    timeout = cfg["request_timeout"]
    header = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/87.0.4280.141',
    }
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, headers=header, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception:
            if attempt == tries:
                raise
            time.sleep(delay)


class ManyThreadDownload:
    def __init__(self):
        self.num = _get_tdx_config()["download"]["thread_count"]
        self.url = ''
        self.name = ''
        self.total = 0

    def get_range(self):
        ranges = []
        offset = int(self.total / self.num)
        for i in range(self.num):
            if i == self.num - 1:
                ranges.append((i * offset, ''))
            else:
                ranges.append((i * offset, (i + 1) * offset - 1))
        return ranges

    def _download_chunk(self, ts_queue):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        # 单 session 复用：urllib3.Retry 自动处理 5xx/429 重试与退避
        cfg = _get_tdx_config()["download"]
        session = requests.Session()
        retry_strategy = Retry(
            total=cfg["http_retry_total"],
            backoff_factor=cfg["http_retry_backoff"],
            status_forcelist=cfg["http_retry_status_codes"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        try:
            while not ts_queue.empty():
                start_, end_ = ts_queue.get()
                headers = {'Range': 'Bytes=%s-%s' % (start_, end_), 'Accept-Encoding': '*'}
                try:
                    res = session.get(self.url, headers=headers, timeout=cfg["chunk_timeout"])
                    res.raise_for_status()
                except Exception as e:
                    logger.error(f"  下载分片 ({start_}-{end_}) 失败: {e}")
                    raise RuntimeError(f"下载分片 ({start_}-{end_}) 失败") from e

                with open(self.name, "rb+") as fd:
                    fd.seek(start_)
                    fd.write(res.content)
                res.close()
        finally:
            session.close()

    def run(self, url, name):
        import requests

        self.url = url
        self.name = name
        self.total = int(requests.head(url).headers['Content-Length'])
        name_path = Path(name)
        if name_path.exists() and name_path.stat().st_size >= self.total:
            return self.total

        with open(name, "wb") as fd:
            fd.truncate(self.total)

        ts_queue = Queue()
        for ran in self.get_range():
            ts_queue.put(ran)

        threads = []
        for i in range(self.num):
            t = threading.Thread(target=self._download_chunk, kwargs={'ts_queue': ts_queue}, daemon=True)
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def historyfinancialreader(filepath):
    """读取通达信专业财务 .dat 文件"""
    with open(filepath, 'rb') as cw_file:
        header_pack_format = '<1hI1H3L'
        header_size = struct.calcsize(header_pack_format)
        stock_item_size = struct.calcsize("<6s1c1L")
        data_header = cw_file.read(header_size)
        stock_header = struct.unpack(header_pack_format, data_header)
        max_count = stock_header[2]
        report_size = stock_header[4]
        report_fields_count = int(report_size / 4)
        report_pack_format = '<{}f'.format(report_fields_count)
        results = []
        for stock_idx in range(max_count):
            cw_file.seek(header_size + stock_idx * struct.calcsize("<6s1c1L"))
            si = cw_file.read(stock_item_size)
            stock_item = struct.unpack("<6s1c1L", si)
            code = stock_item[0].decode("utf-8")
            foa = stock_item[2]
            cw_file.seek(foa)
            info_data = cw_file.read(struct.calcsize(report_pack_format))
            cw_info = list(struct.unpack(report_pack_format, info_data))
            cw_info.insert(0, code)
            results.append(cw_info)
    return pd.DataFrame(results)


def list_cw_files(directory: Path, ext_name: str) -> list[str]:
    """列出目录中 gpcw????????.ext 格式的文件"""
    if not directory.exists():
        return []
    return [
        f.name for f in directory.iterdir()
        if len(f.name) == _CW_FILENAME_LEN and f.name.startswith("gpcw") and f.suffix == f".{ext_name}"
    ]


# ── 下载财务文件 ──────────────────────────────────────────

def sync_cw_files():
    """从通达信服务器下载/更新专业财务文件到 download/ 目录"""
    cw_dir = DOWNLOAD_DIR / "cw"
    pkl_dir = DOWNLOAD_DIR / "cw_pkl"
    cw_dir.mkdir(parents=True, exist_ok=True)
    pkl_dir.mkdir(parents=True, exist_ok=True)

    logger.info("下载通达信财务文件校验信息...")
    resp = download_url(_get_tdx_config()["cw_txt_url"])
    lines = resp.text.strip().split('\r\n')
    rows = [line.strip().split(",") for line in lines]
    server_df = pd.DataFrame(rows, columns=['filename', 'md5', 'filesize'])

    downloader = ManyThreadDownload()

    # 1) 下载缺失的 zip 文件
    local_zips = list_cw_files(cw_dir, 'zip')
    for filename in server_df['filename'].tolist():
        if filename not in local_zips:
            tick = time.time()
            logger.info(f"  {filename} 本机没有，开始下载")
            zip_path = cw_dir / filename
            downloader.run(_get_tdx_config()["cw_file_url"] + filename, str(zip_path))
            if not _extract_and_convert(zip_path, cw_dir, pkl_dir):
                continue
            logger.info(f"  {filename} 完成 用时 {time.time() - tick:.2f}s")

    # 2) 更新 md5 不一致的 zip 文件（服务器 md5 统一转小写，与 hexdigest() 对齐）
    local_zips = list_cw_files(cw_dir, 'zip')
    server_md5_map = dict(zip(server_df['filename'], server_df['md5'].str.lower()))
    for filename in local_zips:
        zip_path = cw_dir / filename
        with open(zip_path, 'rb') as f:
            file_md5 = hashlib.md5(f.read()).hexdigest()
        if file_md5 != server_md5_map.get(filename, ''):
            tick = time.time()
            logger.info(f"  {filename} 需要更新，开始下载")
            zip_path.unlink()
            downloader.run(_get_tdx_config()["cw_file_url"] + filename, str(zip_path))
            if not _extract_and_convert(zip_path, cw_dir, pkl_dir):
                continue
            logger.info(f"  {filename} 完成更新 用时 {time.time() - tick:.2f}s")

    # 3) 补齐缺失的 pkl 导出
    local_dats = list_cw_files(cw_dir, 'dat')
    existing_pkls = {p.name for p in pkl_dir.iterdir()} if pkl_dir.exists() else set()
    for datname in local_dats:
        pkl_name = datname[:-4] + '.pkl'
        if pkl_name not in existing_pkls:
            tick = time.time()
            logger.info(f"  {datname} 导出 pkl")
            df = historyfinancialreader(str(cw_dir / datname))
            df.to_pickle(str(pkl_dir / pkl_name), compression=None)
            logger.info(f"  {datname} 完成 用时 {time.time() - tick:.2f}s")

    logger.info("专业财务文件同步完成")


def _extract_and_convert(zip_path: Path, cw_dir: Path, pkl_dir: Path) -> bool:
    """解压 zip 并转换 dat -> pkl，失败返回 False"""
    try:
        with zipfile.ZipFile(str(zip_path), 'r') as zf:
            zf.extractall(str(cw_dir))
    except (zipfile.BadZipFile, OSError) as e:
        logger.error(f"  文件 {zip_path.name} 损坏或解压失败，跳过: {e}")
        zip_path.unlink(missing_ok=True)
        return False

    dat_path = zip_path.with_suffix('.dat')
    if dat_path.exists():
        df = historyfinancialreader(str(dat_path))
        pkl_path = pkl_dir / (zip_path.stem + '.pkl')
        df.to_pickle(str(pkl_path), compression=None)
    return True


# ── 数据源：三步获取 gbbq ─────────────────────────────────

def _load_gbbq_from_local() -> pd.DataFrame | None:
    """步骤1: 从通达信本地 gbbq 二进制文件读取"""
    import pytdx.reader.gbbq_reader

    if os.name == "nt":
        local_root = get_config().get("local_paths", {}).get("tdx_gbbq")
        if not local_root:
            return None
        gbbq_path = Path(local_root)
    else:
        gbbq_path = Path.home() / "data" / "tdx" / "T0002" / "hq_cache" / "gbbq"

    if not gbbq_path.exists():
        return None

    logger.info("解密通达信 gbbq 股本变迁文件...")
    df = pytdx.reader.gbbq_reader.GbbqReader().get_df(str(gbbq_path))
    df = df.drop(columns=['market'])
    df.columns = ['code', 'date', 'category',
                   'dividend', 'allotment_price', 'bonus_share', 'allotment_share']
    df['category'] = df['category'].astype(str).map(_get_tdx_config()["capital_category"])
    df['code'] = df['code'].astype(str)
    return df


def _load_gbbq_from_binary_cache() -> pd.DataFrame | None:
    """步骤2: 从 csv/gbbq 二进制缓存文件读取（注意：文件无后缀名，是 dbf 二进制不是 CSV）"""
    import pytdx.reader.gbbq_reader

    if not GBBQ_FILE.exists():
        return None
    logger.info(f"从 {GBBQ_FILE} 读取 gbbq 数据")
    df = pytdx.reader.gbbq_reader.GbbqReader().get_df(str(GBBQ_FILE))
    df = df.drop(columns=['market'])
    df.columns = ['code', 'date', 'category',
                   'dividend', 'allotment_price', 'bonus_share', 'allotment_share']
    df['category'] = df['category'].astype(str).map(_get_tdx_config()["capital_category"])
    df['code'] = df['code'].astype(str)
    return df


def _load_gbbq_from_download() -> pd.DataFrame | None:
    """步骤3: 从通达信公开下载地址下载 gbbq.zip 并读取"""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    zip_path = DOWNLOAD_DIR / "gbbq.zip"
    try:
        logger.info(f"下载通达信 gbbq 文件: {_get_tdx_config()['gbbq_zip_url']}")
        resp = download_url(_get_tdx_config()["gbbq_zip_url"])
        zip_path.write_bytes(resp.content)

        with zipfile.ZipFile(str(zip_path), "r") as zf:
            member = next((name for name in zf.namelist() if Path(name).name.lower() == "gbbq"), None)
            if member is None:
                logger.info("gbbq.zip 中未找到 gbbq 文件")
                return None
            GBBQ_FILE.write_bytes(zf.read(member))

        logger.info(f"gbbq 文件已下载到 {GBBQ_FILE}")
    except Exception as e:
        logger.error(f"下载 gbbq 文件失败: {e}")
        return None

    return _load_gbbq_from_binary_cache()


def _load_gbbq_from_server() -> pd.DataFrame | None:
    """步骤4: 通过 pytdx 行情接口从通达信服务器在线拉取"""
    from datasource.tdx import fetch_xdxr_data
    from util import dbutil

    conn = dbutil.get_connection(is_read_only=True)
    try:
        stocks = conn.execute(
            "SELECT symbol, exchange FROM STOCK_INFO WHERE board NOT IN ('INDEX','BOND','ETF')"
        ).fetchall()
    finally:
        conn.close()

    if not stocks:
        logger.error("数据库中无股票列表，请先运行 sync_stock_info")
        return None

    return fetch_xdxr_data(stocks)


# ── 组装：获取 + 保存 + 入库 ──────────────────────────────

def sync_gbbq(download: bool = False):
    """三步获取 gbbq 股本变迁数据，保存 csv 并写入数据库"""
    tick = time.time()

    # 按优先级依次尝试数据源；--download 时下载优先级最高
    if download:
        df_gbbq = _load_gbbq_from_download()
        source = "下载文件"
    else:
        df_gbbq = None
        source = ""

    if df_gbbq is None:
        df_gbbq = _load_gbbq_from_local()
        source = "本地二进制"
    if df_gbbq is None:
        df_gbbq = _load_gbbq_from_binary_cache()
        source = "二进制缓存"
    if df_gbbq is None:
        df_gbbq = _load_gbbq_from_server()
        source = "在线服务器"
    if df_gbbq is None:
        logger.info("所有数据源均不可用，跳过 gbbq 同步")
        return

    logger.info(f"数据来源: {source}，共 {len(df_gbbq)} 条  用时 {time.time() - tick:.2f}s")

    # 保存 gbbq 数据到 csv 目录
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    df_gbbq.to_csv(str(GBBQ_FILE.with_suffix('.csv')), encoding='utf-8', index=False)
    logger.info(f"gbbq 数据已保存到 {GBBQ_FILE.with_suffix('.csv')}")

    # 写入数据库
    save_capital_detail_to_db(df_gbbq)


def cleanup_gbbq_file():
    """删除项目 csv 目录下的 gbbq 二进制缓存文件"""
    if not GBBQ_FILE.exists():
        return
    try:
        GBBQ_FILE.unlink()
        logger.info(f"已删除 gbbq 二进制缓存文件: {GBBQ_FILE}")
    except Exception as e:
        logger.error(f"删除 gbbq 二进制缓存文件失败: {e}")


def save_capital_detail_to_db(df: pd.DataFrame):
    """将股本变迁数据写入 CAPITAL_DETAIL 表"""
    from util import dbutil

    logger.info(f"正在将 {len(df)} 条股本变迁数据写入数据库...")
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        conn.register("temp_capital_detail", df)
        conn.execute("""
            INSERT OR REPLACE INTO CAPITAL_DETAIL
                (code, date, category, dividend, allotment_price,
                 bonus_share, allotment_share, updated_at)
            SELECT
                code,
                CAST(
                    STRPTIME(CAST(CAST(date AS INTEGER) AS VARCHAR), '%Y%m%d')
                    AS DATE
                ),
                category,
                CAST(dividend        AS DOUBLE),
                CAST(allotment_price AS DOUBLE),
                CAST(bonus_share     AS DOUBLE),
                CAST(allotment_share AS DOUBLE),
                now()
            FROM temp_capital_detail
        """)
        logger.info(f"[入库] 成功写入 {len(df)} 条股本变迁数据")
    except Exception as e:
        logger.error(f"写入 CAPITAL_DETAIL 表失败: {e}")
    finally:
        if conn is not None:
            try:
                conn.unregister("temp_capital_detail")
            except Exception:
                pass
            conn.close()


# ── 主入口 ────────────────────────────────────────────────

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "同步股本变迁相关数据到 CAPITAL_DETAIL。\n"
            "程序会先同步通达信专业财务文件(cw)，再获取 gbbq 股本变迁数据，"
            "最终写入数据库。"
        ),
        epilog=(
            "执行内容:\n"
            "  1. 下载或更新 download/cw 下的通达信专业财务文件\n"
            "  2. 生成或补齐对应的 dat/pkl 文件\n"
            "  3. 按优先级读取 gbbq 股本变迁数据\n"
            "  4. 保存到 csv/gbbq.csv 并写入 CAPITAL_DETAIL 表\n\n"
            "gbbq 数据源优先级(默认):\n"
            "  1. 本地通达信目录中的二进制 gbbq 文件\n"
            "  2. 项目 csv/gbbq 文件\n"
            "  3. 在线通达信服务器接口\n\n"
            "gbbq 数据源优先级(加 --download):\n"
            "  1. 从 gbbq.zip 下载\n"
            "  2. 本地通达信目录中的二进制 gbbq 文件\n"
            "  3. 项目 csv/gbbq 文件\n"
            "  4. 在线通达信服务器接口\n\n"
            "示例:\n"
            "  python -m etl.sync_capital\n"
            "  python -m etl.sync_capital --download\n"
            "  python etl/sync_capital.py"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="优先下载 gbbq.zip，默认不下载",
    )
    return parser.parse_args()


def main() -> None:
    configure_etl_logging()
    args = parse_arguments()

    start = time.time()
    logger.info('=' * 60)
    logger.info('股本变迁数据同步任务启动')
    logger.info('=' * 60)

    sync_cw_files()
    sync_gbbq(download=args.download)
    cleanup_gbbq_file()

    logger.info('=' * 60)
    logger.info(f'全部完成 用时 {time.time() - start:.2f}s')
    logger.info('=' * 60)


if __name__ == '__main__':
    main()
