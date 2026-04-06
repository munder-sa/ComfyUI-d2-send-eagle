from typing import Optional, TypedDict, List
import os
import requests
import subprocess
import platform


class FolderInfo(TypedDict):
    id: str
    name: str


class EagleAPI:
    def __init__(self, base_url="http://127.0.0.1:41595"):
        # トークンを先に設定（_check_connection 内で参照するため）
        self.token = os.environ.get(
            "EAGLE_API_TOKEN", "14f17903-a9f9-480b-afb6-d010f2a45fa3"
        )

        # WSL2環境の場合、localhostをWindowsホストのIPに変換を試みる
        # ただし、ミラーモードの場合は 127.0.0.1 が有効なので、接続テストを行う
        if base_url == "http://127.0.0.1:41595" and self._is_wsl():
            if not self._check_connection("http://127.0.0.1:41595"):
                host_ip = self._get_wsl_host_ip()
                if host_ip:
                    target_url = f"http://{host_ip}:41595"
                    if self._check_connection(target_url):
                        base_url = target_url

        self.base_url = base_url
        self.folder_list: Optional[List[FolderInfo]] = None

    def _check_connection(self, url: str) -> bool:
        try:
            # Eagle APIの稼働確認 (タイムアウトを短めに設定)
            response = requests.get(
                f"{url}/api/application/info",
                params={"token": self.token},
                timeout=1.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    def _is_wsl(self):
        return (
            "microsoft-standard" in platform.release().lower()
            or "wsl" in platform.release().lower()
        )

    def _get_wsl_host_ip(self):
        try:
            # WSL2からWindowsホストのIPを取得する一般的な方法
            result = subprocess.run(
                ["sh", "-c", "ip route show | grep default | cut -d' ' -f3"],
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            return None

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
            if response is None:
                return ""
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
            if result is None:
                self.folder_list = []
                return
            self.folder_list = self._extract_id_name_pairs(result["data"])
        except requests.RequestException:
            self.folder_list = []

    # #########################################
    # Private method for sending requests
    def _send_request(self, endpoint, method="GET", data=None):
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json"}
        params = {"token": self.token} if self.token else {}

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=params)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, params=params)
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
