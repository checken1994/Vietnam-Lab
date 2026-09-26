"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
IDataSource Interface - Base interface cho tất cả data sources
"""
from abc import ABC, abstractmethod
from typing import Any


class IDataSource(ABC):
    """
    Interface chuẩn cho tất cả data sources.
    Mỗi lĩnh vực mới cần implement interface này.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tên của data source."""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """Độ ưu tiên (1 = cao nhất)."""
        pass

    @property
    @abstractmethod
    def ttl(self) -> int:
        """Cache TTL tính bằng giây."""
        pass

    @abstractmethod
    def get_supported_intents(self) -> list[str]:
        """Trả về danh sách intents mà source này hỗ trợ."""
        pass

    @abstractmethod
    def can_handle(self, intent: str, entity: str | None = None) -> bool:
        """Kiểm tra xem source này có xử lý được intent không."""
        pass

    @abstractmethod
    def fetch(self, intent: str, entity: str, **kwargs) -> dict[str, Any] | None:
        """
        Lấy dữ liệu thực để xác minh.

        Args:
            intent: Loại câu hỏi (ví dụ: 'molecular_weight', 'exchange_rate')
            entity: Thực thể cần tra cứu (ví dụ: 'water', 'USD/EUR')
            **kwargs: Các tham số bổ sung

        Returns:
            Dict với keys:
                - value: Giá trị thực
                - source: Tên nguồn dữ liệu
                - metadata: Thông tin bổ sung (optional)
            None nếu không tìm thấy
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Kiểm tra xem data source có hoạt động không."""
        pass
