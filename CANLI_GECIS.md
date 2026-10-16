# Canlıya geçiş kontrol listesi (BIST)

Bu sistem bugün **emir göndermez**: yalnızca `data/orders.json` yazar. Aracı kuruma bağlamak için ayrı bir yürütme
katmanı yazılması gerekir. O katman yazılırken aşağıdakilerin HEPSİ zorunludur (us_f1_bot ve karma_perp_bot'ta
bulunan korumalar):

1. **Elle açılan pozisyonlar korunur:** yalnızca botun aldığı hisselere dokunulur (sahiplik listesi tutulur).
2. **Çift emir yok:** `orders.json` içindeki `id` bir kez uygulanır; açık emir varsa yeni emir gönderilmez.
3. **Eski emir yok:** `execute_date` bugün değilse uygulanmaz.
4. **Açıklanamayan sıçrama kilidi:** kasa bir günde %30+ değiştiyse o gün emir yok, Telegram uyarısı.
5. **Test dışı düşüş freni:** dolar bazında zirveden düşüş testteki en kötüsünü (-%50) aşarsa kullanılan pay yarıya iner.
6. **Tek emir ve günlük toplam tutar sınırı** (yanlış adet/fiyat hatasına karşı).
7. **Tavan/taban ve işlem görmeyen hisse:** emir iptal, ertesi gün yeniden denenir.
8. **Bekçi:** `watchdog.yml` (var) + emir katmanı için ayrı "emirler uygulandı mı" denetimi.
9. **Testler:** sahte aracı kurumla 1–8 test edilir; test başarısızsa emir gönderilmez.
10. **Önce kâğıt hesap:** en az 1 ay aracı kurumun demo hesabında, sonra küçük tutarla gerçek hesap.
