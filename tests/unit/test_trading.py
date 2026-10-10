"""Test unitari di `plan_trade` (`decision_engine/trading.py`): la matematica
di un BUY/SELL, pura, senza DB. I casi sono quelli dei vecchi test di
integrazione di `execute_trade`."""

from __future__ import annotations

import pytest

from marketmind_llm_decision_engine.decision_engine.trading import PositionState, plan_trade


class TestBuy:
    def test_nuova_posizione_e_cash_scalato(self):
        plan = plan_trade(cash=10_000.0, position=None, action="BUY", size_pct=0.25, price=100.0)

        assert plan.cash_after == pytest.approx(7_500.0)
        assert plan.position_after == PositionState(quantity=25.0, avg_price=100.0)
        assert plan.changed

    def test_su_posizione_esistente_prezzo_medio_ponderato(self):
        plan = plan_trade(
            cash=10_000.0, position=PositionState(quantity=10.0, avg_price=100.0),
            action="BUY", size_pct=0.2, price=200.0,
        )

        # 2.000 di cash a 200 = 10 quote nuove; media (10*100 + 10*200) / 20 = 150
        assert plan.cash_after == pytest.approx(8_000.0)
        assert plan.position_after.quantity == pytest.approx(20.0)
        assert plan.position_after.avg_price == pytest.approx(150.0)

    def test_size_zero_non_cambia_nulla(self):
        position = PositionState(quantity=5.0, avg_price=10.0)

        plan = plan_trade(cash=1_000.0, position=position, action="BUY", size_pct=0.0, price=10.0)

        assert not plan.changed
        assert (plan.cash_after, plan.position_after) == (1_000.0, position)

    def test_senza_cash_non_cambia_nulla(self):
        assert not plan_trade(cash=0.0, position=None, action="BUY", size_pct=0.5, price=10.0).changed


class TestSell:
    def test_vendita_parziale(self):
        plan = plan_trade(
            cash=1_000.0, position=PositionState(quantity=10.0, avg_price=50.0),
            action="SELL", size_pct=0.4, price=80.0,
        )

        assert plan.cash_after == pytest.approx(1_000.0 + 4 * 80.0)
        assert plan.position_after == PositionState(quantity=pytest.approx(6.0), avg_price=50.0)

    def test_vendita_totale_chiude_la_posizione(self):
        plan = plan_trade(
            cash=0.0, position=PositionState(quantity=10.0, avg_price=50.0),
            action="SELL", size_pct=1.0, price=60.0,
        )

        assert plan.cash_after == pytest.approx(600.0)
        assert plan.position_after is None
        assert plan.changed

    def test_senza_posizione_non_cambia_nulla(self):
        plan = plan_trade(cash=500.0, position=None, action="SELL", size_pct=0.5, price=10.0)

        assert not plan.changed
        assert plan.position_after is None


class TestValidazione:
    def test_hold_non_e_un_trade(self):
        with pytest.raises(ValueError, match="HOLD"):
            plan_trade(cash=1.0, position=None, action="HOLD", size_pct=None, price=1.0)

    @pytest.mark.parametrize("price", [0.0, -1.0])
    def test_prezzo_non_positivo_rifiutato(self, price):
        with pytest.raises(ValueError, match="prezzo"):
            plan_trade(cash=1.0, position=None, action="BUY", size_pct=0.5, price=price)

    @pytest.mark.parametrize("size_pct", [None, -0.1, 1.1])
    def test_size_fuori_intervallo_rifiutata(self, size_pct):
        with pytest.raises(ValueError, match="size_pct"):
            plan_trade(cash=1.0, position=None, action="BUY", size_pct=size_pct, price=1.0)
