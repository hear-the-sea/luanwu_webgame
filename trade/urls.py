from django.urls import path

from trade import views

app_name = "trade"

urlpatterns = [
    path("", views.TradeView.as_view(), name="trade"),
    path("shop/buy/", views.shop_buy_view, name="shop_buy"),
    path("shop/sell/", views.shop_sell_view, name="shop_sell"),
    path("bank/exchange-gold-bar/", views.exchange_gold_bar_view, name="exchange_gold_bar"),
    path("bank/troop/deposit/", views.deposit_troop_to_bank_view, name="deposit_troop_to_bank"),
    path("bank/troop/withdraw/", views.withdraw_troop_from_bank_view, name="withdraw_troop_from_bank"),
    path("bank/troop/deposit-all/", views.deposit_all_troops_to_bank_view, name="deposit_all_troops_to_bank"),
    path("bank/troop/withdraw-all/", views.withdraw_all_troops_from_bank_view, name="withdraw_all_troops_from_bank"),
    # 交易行路由
    path("market/create/", views.market_create_listing_view, name="market_create_listing"),
    path("market/purchase/<int:listing_id>/", views.market_purchase_view, name="market_purchase"),
    path("market/cancel/<int:listing_id>/", views.market_cancel_view, name="market_cancel"),
    # 拍卖行路由
    path("auction/bid/<int:slot_id>/", views.auction_bid_view, name="auction_bid"),
]
