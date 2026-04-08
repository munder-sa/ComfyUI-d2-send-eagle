from typing import Optional, TypedDict, List
import os
import subprocess
import requests


class FolderInfo(TypedDict):
    id: str
    name: str


class EagleAPI:
    def __init__(self, base_url="http://127.0.0.1:41595"):
        # トークンの初期化を最優先
        self.token = os.environ.get(
            "EAGLE_API_TOKEN", "14f17903-a9f9-480b-afb6-d010f2a45fa3"
        )
        self._base_url_default = base_url
        self._base_url: Optional[str] = None  # 遅延評価用: None = 未解決
        self.folder_list: Optional[List[FolderInfo]] = None

    # #########################################
    # base_url を遅延評価で解決する
    def _resolve_base_url(self) -> str:
        """初回呼び出し時のみ WSL IP を解決してキャッシュする"""
        if self._base_url is None:
            self._base_url = self._base_url_default
            # WSL環境の場合、デフォルトゲートウェイ(Windowsホスト)のIPを自動取得
            if self._is_wsl():
                try:
                    result = subprocess.run(
                        ["ip", "route", "show", "default"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    if result.stdout:
                        parts = result.stdout.split()
                        if "via" in parts:
                            wsl_ip = parts[parts.index("via") + 1]
                            self._base_url = f"http://{wsl_ip}:41595"
                            print(  # noqa: T201
                                f"[Eagle API] WSL環境を検出: Windowsホスト {wsl_ip} を使用します"
                            )
                except Exception as e:
                    print(f"[Eagle API] WSLホストIPの取得に失敗しました: {e}")  # noqa: T201
                    # フォールバック: /etc/resolv.conf から nameserver を取得
                    self._base_url = (
                        self._get_ip_from_resolv_conf() or self._base_url_default
                    )
        return self._base_url

    # #########################################
    # /etc/resolv.conf から nameserver を抽出してIPを取得
    def _get_ip_from_resolv_conf(self) -> Optional[str]:
        """resolv.conf の nameserver から IP を抽出"""
        try:
            with open("/etc/resolv.conf", "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            ip = parts[1]
                            self_base_url = f"http://{ip}:41595"
                            print(  # noqa: T201
                                f"[Eagle API] /etc/resolv.conf から nameserver {ip} を使用します"
                            )
                            return self_base_url
        except Exception as e:
            print(f"[Eagle API] /etc/resolv.conf の読み込みに失敗しました: {e}")  # noqa: T201
        return None

    # #########################################
    # WSL環境かどうかを判定
    @staticmethod
    def _is_wsl() -> bool:
        try:
            with open("/proc/version", "r") as f:
                content = f.read().lower()
                return "microsoft" in content or "wsl" in content
        except OSError:
            return False

    # #########################################
    # 画像をEagleに送信
    def add_item_from_path(self, data, folder_id=None):
        if folder_id:
            data["folderId"] = folder_id
        return self._send_request("/api/item/addFromPath", method="POST", data=data)

    # #########################################
    # フォルダ名 or ID で該当フォルダを探してIDを返す
    # 存在しなければ作成してIDを返す
    def find_or_create_folder(self, name_or_id: str) -> str:
        folder = self._find_folder(name_or_id)

        if folder:
            return folder.get("id", "")
        return self._create_folder(name_or_id)

    # #########################################
    # フォルダ名 or ID で該当フォルダを取得
    # 存在しないなら None を返す
    def _find_folder(self, name_or_id: str) -> Optional[FolderInfo]:
        self._ensure_folder_list()

        if self.folder_list is not None:
            # 名前とIDの両方で検索
            for folder in self.folder_list:
                if folder["name"] == name_or_id or folder["id"] == name_or_id:
                    return folder

        return None

    # #########################################
    # フォルダを作成
    # 作成できない or 名前指定がなければ "" を返す
    def _create_folder(self, name: str) -> str:
        if not name:
            return ""

        try:
            data = {"folderName": name}
            response = self._send_request(
                "/api/folder/create", method="POST", data=data
            )
            new_folder_id = response.get("data", {}).get("id", "")

            # フォルダリストを更新
            if new_folder_id and self.folder_list is not None:
                self.folder_list.append({"id": new_folder_id, "name": name})

            return new_folder_id

        except requests.RequestException:
            return ""

    # #########################################
    # Eagle のフォルダID、名前の一覧を取得
    def _ensure_folder_list(self):
        if self.folder_list is None:
            self._get_all_folder_list()

    def _get_all_folder_list(self):
        try:
            result = self._send_request("/api/folder/list")
            self.folder_list = self._extract_id_name_pairs(result["data"])
        except requests.RequestException:
            self.folder_list = []

    # #########################################
    # Private method for sending requests
    def _send_request(self, endpoint, method="GET", data=None):
        url = self._resolve_base_url() + endpoint
        headers = {"Content-Type": "application/json"}
        params = {"token": self.token} if self.token else {}

        try:
            if method == "GET":
                response = requests.get(
                    url, headers=headers, params=params, timeout=5.0
                )
            elif method == "POST":
                response = requests.post(
                    url, headers=headers, json=data, params=params, timeout=5.0
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.ConnectionError:
            print(  # noqa: T201
                "[Eagle API] 接続に失敗しました。Eagleが起動しているか確認してください。登録をスキップします"
            )
            return None
        except requests.RequestException as e:
            print(f"Eagle request failed: {e}")  # noqa: T201
            raise

    # #########################################
    # フォルダリストを作成
    def _extract_id_name_pairs(self, data):
        result = []

        def recursive_extract(item):
            if isinstance(item, dict):
                if "id" in item and "name" in item:
                    result.append({"id": item["id"], "name": item["name"]})
                if "children" in item and isinstance(item["children"], list):
                    for child in item["children"]:
                        recursive_extract(child)
            elif isinstance(item, list):
                for element in item:
                    recursive_extract(element)

        recursive_extract(data)
        return result
