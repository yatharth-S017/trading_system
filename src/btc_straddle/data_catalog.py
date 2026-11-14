"""
NautilusTrader ParquetDataCatalog integration for market data persistence.

This module provides a clean, modular manager around NautilusTrader's native
ParquetDataCatalog (version 1.231.0) to persist streaming QuoteTicks and
Instrument definitions for historical analysis and backtesting.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog

logger = logging.getLogger(__name__)


class StraddleDataCatalog:
    """
    Manages persistent storage of market data and instruments in a ParquetDataCatalog.

    Incoming QuoteTicks are buffered in memory and written to disk in chunks to avoid
    excessive disk I/O and interval fragmentation. Instrument definitions are persisted
    alongside market data so the catalog can be used directly for backtesting.

    Parameters
    ----------
    catalog_path : str | Path, default "data/catalog"
        Filesystem path to the catalog root directory.
    batch_size : int, default 50
        Number of quotes to accumulate in memory before flushing to disk.
    """

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog",
        batch_size: int = 50,
    ) -> None:
        self._path = Path(catalog_path).resolve()
        self._batch_size = max(1, batch_size)
        self._quote_buffer: list[QuoteTick] = []
        self._recorded_instruments: set[str] = set()

        # Ensure directory exists before initializing Nautilus catalog
        try:
            self._path.mkdir(parents=True, exist_ok=True)
            self._catalog = ParquetDataCatalog(path=str(self._path))
            logger.info(f"Initialized ParquetDataCatalog at: {self._path} (batch_size={self._batch_size})")
        except Exception as e:
            logger.error(f"Failed to initialize ParquetDataCatalog at {self._path}: {e}")
            raise

    @property
    def catalog_path(self) -> Path:
        """Return the absolute path to the catalog directory."""
        return self._path

    @property
    def buffer_size(self) -> int:
        """Return the number of unwritten quotes currently in the buffer."""
        return len(self._quote_buffer)

    def record_quote(self, tick: QuoteTick) -> None:
        """
        Buffer an incoming QuoteTick and flush to disk if batch_size is reached.

        Parameters
        ----------
        tick : QuoteTick
            The market data quote tick to persist.
        """
        try:
            self._quote_buffer.append(tick)
            if len(self._quote_buffer) >= self._batch_size:
                self.flush()
        except Exception as e:
            logger.warning(
                f"DataCatalog: Failed to buffer quote for {getattr(tick, 'instrument_id', 'unknown')}: {e}"
            )

    def record_instruments(self, instruments: Sequence[Instrument]) -> None:
        """
        Persist instrument definitions to the catalog if not already recorded.

        Parameters
        ----------
        instruments : Sequence[Instrument]
            NautilusTrader instrument objects to store in the catalog.
        """
        to_write = []
        for inst in instruments:
            if inst is None:
                continue
            inst_id_str = str(inst.id)
            if inst_id_str not in self._recorded_instruments:
                to_write.append(inst)
                self._recorded_instruments.add(inst_id_str)

        if not to_write:
            return

        try:
            self._catalog.write_data(to_write)
            logger.info(
                f"DataCatalog: Persisted {len(to_write)} instrument definition(s): "
                f"{', '.join(str(i.id) for i in to_write)}"
            )
        except Exception as e:
            logger.warning(f"DataCatalog: Failed to persist instruments: {e}")

    def flush(self) -> int:
        """
        Flush all buffered QuoteTicks to Parquet storage on disk.

        Returns
        -------
        int
            The number of quotes written to disk.
        """
        if not self._quote_buffer:
            return 0

        quotes_to_write = list(self._quote_buffer)
        self._quote_buffer.clear()

        try:
            self._catalog.write_data(quotes_to_write)
            logger.info(
                f"DataCatalog: Flushed {len(quotes_to_write)} QuoteTicks to Parquet catalog at {self._path}"
            )
            return len(quotes_to_write)
        except Exception as e:
            logger.error(
                f"DataCatalog: Failed to flush {len(quotes_to_write)} QuoteTicks to disk: {e}"
            )
            # Re-queue on failure so ticks are not permanently lost
            self._quote_buffer = quotes_to_write + self._quote_buffer
            return 0

    def read_quotes(
        self,
        instrument_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[QuoteTick]:
        """
        Query and return persisted QuoteTicks from the Parquet catalog.

        Parameters
        ----------
        instrument_ids : list[str], optional
            Filter for specific instrument ID strings. If None, all quotes are returned.
        **kwargs : Any
            Additional query filters passed to ParquetDataCatalog.quote_ticks (e.g. start, end).

        Returns
        -------
        list[QuoteTick]
            Matching QuoteTick objects ordered by timestamp.
        """
        try:
            return self._catalog.quote_ticks(instrument_ids=instrument_ids, **kwargs)
        except Exception as e:
            logger.error(f"DataCatalog: Failed to read quotes from {self._path}: {e}")
            return []

    def read_instruments(
        self,
        instrument_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[Instrument]:
        """
        Query and return persisted Instrument definitions from the catalog.

        Parameters
        ----------
        instrument_ids : list[str], optional
            Filter for specific instrument ID strings. If None, all instruments are returned.
        **kwargs : Any
            Additional query filters passed to ParquetDataCatalog.instruments.

        Returns
        -------
        list[Instrument]
            Matching Instrument objects.
        """
        try:
            return self._catalog.instruments(instrument_ids=instrument_ids, **kwargs)
        except Exception as e:
            logger.error(f"DataCatalog: Failed to read instruments from {self._path}: {e}")
            return []
