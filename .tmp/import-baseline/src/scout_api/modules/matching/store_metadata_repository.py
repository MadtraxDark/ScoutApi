from sqlalchemy.orm import Session

from scout_api.modules.matching.models import StoreMetadata


class StoreMetadataRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_all(self) -> dict[str, StoreMetadata]:
        return {row.store_key: row for row in self.session.query(StoreMetadata).all()}

    def get(self, key: str) -> StoreMetadata | None:
        return self.session.get(StoreMetadata, key)

    def save(self, key: str, display_name: str, logo_svg: str | None) -> StoreMetadata:
        row = self.get(key)
        if row is None:
            row = StoreMetadata(store_key=key)
            self.session.add(row)
        row.display_name = display_name
        if logo_svg is not None:
            row.logo_svg = logo_svg
            row.logo_mime_type = "image/svg+xml"
            row.logo_original_file_id = None
            row.logo_optimized_file_id = None
            row.logo_processing_status = "ready"
            row.logo_version = None
            row.logo_processing_error = None
        self.session.commit()
        self.session.refresh(row)
        return row

    def save_logo_upload(
        self,
        key: str,
        *,
        mime_type: str,
        original_file_id: str,
        version: str,
        status: str,
        svg_text: str | None = None,
    ) -> StoreMetadata:
        row = self.get(key)
        if row is None:
            row = StoreMetadata(store_key=key)
            self.session.add(row)
        row.logo_svg = svg_text
        row.logo_mime_type = mime_type
        row.logo_original_file_id = original_file_id
        row.logo_optimized_file_id = None
        row.logo_processing_status = status
        row.logo_version = version
        row.logo_processing_error = None
        self.session.commit()
        self.session.refresh(row)
        return row
