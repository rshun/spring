# 修改记录:
#   2026-05-26  Claude  从 etl/sync_capital.py 拆分通达信"离线文件"数据源逻辑
#   2026-05-26  Claude  加固 ManyThreadDownload: 收集线程异常 + run 后校验 size，
#                       sync_cw_files 单文件失败跳过，避免静默产出错误大小的 zip
#   2026-05-29  Claude  新增 iter_cw_reports: 遍历本地 cw 文件按报告期产出 DataFrame
#   2026-06-12  Claude  cw md5 校验按报告期截断(默认最近12个季度)，规避服务器每日
#                       重打包历史 zip 导致的 md5 滚动变化; full=True 恢复全量校验
"""
通达信离线文件数据源

对比 datasource/tdx.py (pytdx 在线 socket 行情接口)，本模块负责通过 HTTP
从通达信文件服务器下载离线数据文件，并解析为 DataFrame：
  1. cw 专业财务文件 (gpcwYYYYMMDD.zip / .dat)
  2. gbbq 股本变迁文件 (gbbq 二进制)

对外接口:
  - sync_cw_files()              : 下载/更新 cw 文件，并导出 pkl
  - fetch_gbbq(download=False)   : 按优先级获取 gbbq DataFrame
  - cleanup_gbbq_file()          : 清理项目内的 gbbq 二进制缓存

注意事项:
  1. download_url / ManyThreadDownload 是仅本模块使用的 HTTP 下载工具，
     若后续被第二个调用方需要，再抽取到 util/ 公共模块。
  2. fetch_gbbq 内含 4 个数据源回退路径，详见函数 docstring。
"""
import hashlib
import logging
import os
import struct
import threading
import time
import zipfile
from pathlib import Path
from queue import Queue

import pandas as pd

from util.config import get_config

logger = logging.getLogger("etl.datasource.tdx_offline")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = PROJECT_ROOT / "download"
CSV_DIR      = PROJECT_ROOT / "csv"
GBBQ_FILE    = CSV_DIR / "gbbq"

# 通达信财务文件名格式: "gpcw" + "YYYYMMDD" + ".ext" = 4+8+4 = 16 字符
_CW_FILENAME_LEN = 16


def _get_tdx_config():
    """从 config.yaml 读取通达信财务相关配置"""
    return get_config()["tdx"]


# ── HTTP 下载工具 ────────────────────────────────────────

def download_url(url):
    """单文件 HTTP 下载，带重试"""
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
    """多线程 Range 分片下载器（仅供 cw 文件下载使用）"""

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

    def _download_chunk(self, ts_queue, errors):
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
                    with open(self.name, "rb+") as fd:
                        fd.seek(start_)
                        fd.write(res.content)
                    res.close()
                except Exception as e:
                    # 不再 raise: 线程内抛出会被静默吞掉，导致主线程认为下载成功
                    logger.error(f"  下载分片 ({start_}-{end_}) 失败: {e}")
                    errors.append(e)
                    return
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

        errors: list[Exception] = []
        threads = []
        for i in range(self.num):
            t = threading.Thread(
                target=self._download_chunk,
                kwargs={'ts_queue': ts_queue, 'errors': errors},
                daemon=True,
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 任一分片失败 → 删除残缺文件并抛出，由调用方决定跳过/重试
        if errors:
            name_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"下载 {url} 失败: {len(errors)} 个分片出错，首个错误: {errors[0]}"
            ) from errors[0]

        # 防御性 size 校验：分片即使全部"成功"也校验最终落盘大小
        actual_size = name_path.stat().st_size
        if actual_size != self.total:
            name_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"下载 {url} 大小不符: 实际 {actual_size}, 预期 {self.total}"
            )


# ── 二进制文件解析 ────────────────────────────────────────

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
    """列出目录中 gpcwYYYYMMDD.<ext_name> 格式的通达信财务文件"""
    if not directory.exists():
        return []
    return [
        f.name for f in directory.iterdir()
        if len(f.name) == _CW_FILENAME_LEN and f.name.startswith("gpcw") and f.suffix == f".{ext_name}"
    ]


# ── cw 专业财务文件同步 ──────────────────────────────────

def filter_recent_cw_filenames(filenames: list[str], quarters: int) -> set[str]:
    """从 gpcwYYYYMMDD.* 文件名中选出最近 quarters 个报告期的文件名集合

    quarters <= 0 表示不截断(返回全部)；无法解析报告期的文件名安全起见保留。
    """
    if quarters <= 0:
        return set(filenames)
    dated: dict[str, set[str]] = {}
    undated: set[str] = set()
    for name in filenames:
        rd = name[4:12] if len(name) == _CW_FILENAME_LEN and name.startswith("gpcw") else ""
        if rd.isdigit():
            dated.setdefault(rd, set()).add(name)
        else:
            undated.add(name)
    keep = undated
    for rd in sorted(dated)[-quarters:]:
        keep |= dated[rd]
    return keep


def sync_cw_files(full: bool = False):
    """从通达信服务器下载/更新专业财务文件到 download/ 目录

    服务器每天会批量重新打包大部分历史报告期的 zip(md5 滚动变化, 内容多为
    无实质变更), 全量 md5 校验会导致每天重下约百个历史文件。因此默认只对
    最近 N 个季度(config: tdx.cw_refresh_quarters, 默认 12)做 md5 校验更新,
    覆盖披露期更新/财报更正/新股 IPO 近 3 年财务回填; 更早的报告期本地存在
    即跳过。本地缺失的文件不受窗口限制, 始终下载。

    Parameters
    ----------
    full : True 时恢复对全部报告期做 md5 校验(捞超过窗口的陈年重述)。
    """
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
            try:
                downloader.run(_get_tdx_config()["cw_file_url"] + filename, str(zip_path))
            except RuntimeError as e:
                logger.error(f"  {filename} 下载失败，跳过: {e}")
                continue
            if not _extract_and_convert(zip_path, cw_dir, pkl_dir):
                continue
            logger.info(f"  {filename} 完成 用时 {time.time() - tick:.2f}s")

    # 2) 更新 md5 不一致的 zip 文件（服务器 md5 统一转小写，与 hexdigest() 对齐）
    local_zips = list_cw_files(cw_dir, 'zip')
    server_md5_map = dict(zip(server_df['filename'], server_df['md5'].str.lower()))
    if not full:
        quarters = _get_tdx_config().get("cw_refresh_quarters", 12)
        window = filter_recent_cw_filenames(server_df['filename'].tolist(), quarters)
        skipped = [f for f in local_zips if f not in window]
        local_zips = [f for f in local_zips if f in window]
        if skipped:
            logger.info(
                f"  md5 校验按报告期截断: 仅校验最近 {quarters} 个季度共 {len(local_zips)} 个文件, "
                f"跳过 {len(skipped)} 个早期文件 (--full 可全量校验)"
            )
    for filename in local_zips:
        zip_path = cw_dir / filename
        with open(zip_path, 'rb') as f:
            file_md5 = hashlib.md5(f.read()).hexdigest()
        if file_md5 != server_md5_map.get(filename, ''):
            tick = time.time()
            logger.info(f"  {filename} 需要更新，开始下载")
            zip_path.unlink()
            try:
                downloader.run(_get_tdx_config()["cw_file_url"] + filename, str(zip_path))
            except RuntimeError as e:
                logger.error(f"  {filename} 下载失败，跳过本次更新: {e}")
                continue
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


def iter_cw_reports(start: str | None = None, end: str | None = None):
    """遍历本地 cw 专业财务文件，按报告期升序产出 (report_date, DataFrame)

    优先读 download/cw_pkl/*.pkl(已解析)，该期 pkl 缺失时回退 download/cw/*.dat。

    Parameters
    ----------
    start / end : 报告期过滤(闭区间)，接受 YYYYMMDD 或 YYYY-MM-DD；None 表示不限。

    Yields
    ------
    (report_date, df) : report_date 为 YYYYMMDD 字符串(取自文件名 gpcwYYYYMMDD)；
                        df 为位置列 DataFrame(列 0=code, 列 N=cw字段N)。
                        无任何本地文件时不产出(安全降级)。
    """
    pkl_dir = DOWNLOAD_DIR / "cw_pkl"
    cw_dir = DOWNLOAD_DIR / "cw"

    def _norm(d: str | None) -> str | None:
        return d.replace("-", "") if d else d

    s, e = _norm(start), _norm(end)

    # 报告期 = pkl 与 dat 文件名的并集(gpcwYYYYMMDD -> YYYYMMDD)
    names = set(list_cw_files(pkl_dir, "pkl")) | set(list_cw_files(cw_dir, "dat"))
    report_dates = sorted({n[4:12] for n in names})
    if not report_dates:
        logger.warning("未找到任何本地 cw 财务文件(download/cw_pkl 或 download/cw)")
        return

    for rd in report_dates:
        if s and rd < s:
            continue
        if e and rd > e:
            continue
        pkl_path = pkl_dir / f"gpcw{rd}.pkl"
        try:
            if pkl_path.exists():
                df = pd.read_pickle(str(pkl_path))
            else:
                df = historyfinancialreader(str(cw_dir / f"gpcw{rd}.dat"))
        except Exception as ex:
            logger.warning(f"  读取 cw 报告期 {rd} 失败，跳过: {ex}")
            continue
        yield rd, df


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


# ── gbbq 股本变迁数据源 ───────────────────────────────────

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


def fetch_gbbq(download: bool = False) -> pd.DataFrame | None:
    """按优先级获取 gbbq 股本变迁数据并返回 DataFrame

    优先级 (默认):
      1. 本地通达信目录中的 gbbq 二进制文件
      2. 项目 csv/gbbq 二进制缓存
      3. 在线通达信服务器接口 (pytdx)

    优先级 (download=True):
      1. 从 gbbq.zip 下载
      2. 本地通达信目录中的 gbbq 二进制文件
      3. 项目 csv/gbbq 二进制缓存
      4. 在线通达信服务器接口 (pytdx)
    """
    df = None
    source = ""

    if download:
        df = _load_gbbq_from_download()
        source = "下载文件"

    if df is None:
        df = _load_gbbq_from_local()
        source = "本地二进制"
    if df is None:
        df = _load_gbbq_from_binary_cache()
        source = "二进制缓存"
    if df is None:
        df = _load_gbbq_from_server()
        source = "在线服务器"

    if df is None:
        logger.info("所有数据源均不可用，未获取到 gbbq 数据")
        return None

    logger.info(f"gbbq 数据来源: {source}，共 {len(df)} 条")

    # 落 csv 留存
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = GBBQ_FILE.with_suffix('.csv')
    df.to_csv(str(csv_path), encoding='utf-8', index=False)
    logger.info(f"gbbq 数据已保存到 {csv_path}")

    return df


def cleanup_gbbq_file():
    """删除项目 csv 目录下的 gbbq 二进制缓存文件"""
    if not GBBQ_FILE.exists():
        return
    try:
        GBBQ_FILE.unlink()
        logger.info(f"已删除 gbbq 二进制缓存文件: {GBBQ_FILE}")
    except Exception as e:
        logger.error(f"删除 gbbq 二进制缓存文件失败: {e}")
