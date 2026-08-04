# Palimpsest

Kendi bilgisayarınızda çalışan, yerel bir kitap çeviri aracı. Elyazmanız ve
API anahtarınız, yalnızca çeviri çağrılarının kendisi dışında makinenizden
hiç çıkmaz. Araç kitabı bir kez okuyup sizin kontrolünüzdeki bir brief ve
terim listesi çıkarır, ardından paragraf paragraf çevirir — her paragrafı
bağlam içinde taslak hazırlayıp gözden geçirip düzelterek — ve çalışmanızı
otomatik olarak kaydeder.

---

## İlk kurulum (yaklaşık beş dakika, bir kez)

1. **Dosyaları indirin.** Bu sayfanın üstündeki yeşil **Code** butonuna,
   ardından **Download ZIP**'e tıklayıp bilgisayarınızda bir klasöre çıkarın.

   Git kullanıyorsanız `git clone` ile de indirebilirsiniz — bu durumda
   başlatıcı (`start.command`/`start.bat`) her açılışta güncellemeleri
   otomatik çeker, elle bir şey yapmanız gerekmez. Kitaplarınız ve API
   anahtarınız bundan hiç etkilenmez (`projects/` ve `key.txt` git'in
   takip ettiği dosyalar değil).

2. Bilgisayarınızda yoksa **Python 3.10 veya üstünü** https://python.org
   adresinden kurun. (Windows'ta kurulum sırasında "Add Python to PATH"
   kutusunu işaretleyin.)

3. **API anahtarınızı ekleyin.** Uygulamayı ilk açtığınızda sağ üstteki
   **API Anahtarları**'na girin ve kullanmak istediğiniz sağlayıcının (Claude,
   ChatGPT, Gemini) anahtarını yapıştırıp *Kaydet*'e basın. Anahtar,
   bilgisayarınızda `~/.palimpsest` klasöründe şifreli olarak saklanır ve
   hiçbir zaman bilgisayarınızın dışına çıkmaz — yalnızca çeviri
   çağrılarının kendisi ilgili sağlayıcıya gider. Birden fazla sağlayıcının
   anahtarını aynı anda kaydedebilir ve paragraf paragraf aralarında
   geçiş yapabilirsiniz.

   (Eski kurulumlardan kalan `key.txt` dosyası varsa, Anthropic için hâlâ
   çalışmaya devam eder — yeniden anahtar girmeniz gerekmez.)

4. Hepsi bu kadar. Başlatıcı, ihtiyaç duyduğu kütüphaneleri ilk
   çalıştırmada sizin için kurar.

---

## Kullanmak için (her seferinde)

- **Mac:** **`start.command`** dosyasına çift tıklayın
- **Windows:** **`start.bat`** dosyasına çift tıklayın

Tarayıcınız uygulamada otomatik açılır. İşiniz bitince tarayıcı sekmesini
kapatın ve başlatıcının açtığı küçük terminal penceresini kapatın.

> Mac'te `start.command` ilk seferde açılmazsa, sağ tıklayın → Aç → Aç.
> Bunu yalnızca bir kez yapmanız gerekir.

---

## Nasıl çalışır

1. **Bir kitap başlatın.** Bir ad verin, dilleri ayarlayın, bir `.txt`, `.md`
   veya `.docx` dosyası bırakın ve *Kitabı oku & bağlam oluştur*'a tıklayın.
2. **Bağlamı gözden geçirin.** Otomatik oluşturulan brief ve terim listesini
   düzenleyin. Bu, her paragrafla birlikte gider. İsterseniz eşleştirmek için
   bir stil örneği yapıştırın.
3. **Çevirin.** Kaynak paragraflar solda, çevirileriniz sağda durur. Bir
   paragrafta *Çevir*'e tıklayarak taslak→inceleme→düzeltme döngüsünü
   çalıştırın. Sonucu düzenleyin, sonra *Onayla*. Kenar notu, incelemenin
   neyi yakaladığını gösterir.
4. **Kendi kendini kaydeder.** Her değişiklik diske yazılır. İstediğiniz
   zaman kapatın; kitaplarınız, aylar sonra bile, döndüğünüzde *Kitaplarınız*
   ekranında sizi bekliyor olur.
5. **Dışa aktarma**, tamamlanmış çevirinizi bir metin dosyasına yazar.

Zor bir paragrafta modelin daha uzun düşünmesini istiyorsanız, paragrafın
yanındaki ikinci açılır menüden **Düşük/Orta/Yüksek** efor seçebilirsiniz
(yalnızca bunu destekleyen modellerde görünür). Varsayılan her zaman
**Kapalı**'dır — daha yüksek efor ek maliyete yol açar.

---

## Çalışmanız nerede tutulur

Her kitap, **`~/.palimpsest/projects/`** klasöründe bir JSON dosyasıdır —
API anahtarlarınızın ve tercihlerinizin durduğu yerin hemen yanında, uygulama
klasörünün dışında. Çalışmanızı yedeklemek veya taşımak için o klasörü
kopyalayın. Bir projeyi başkasına vermek için tek JSON dosyasını gönderin.

(Eski bir kurulumdan güncelliyorsanız: kitaplarınız uygulama klasörünün
içindeki `projects/`'ta ise, ilk açılışta otomatik olarak buraya taşınır.)

---

## Bir şey çalışmazsa

- **"No API key found"** — API Anahtarları ekranından en az bir sağlayıcı için
  anahtar girdiğinizden emin olun, sonra sayfayı yenileyin.
- **Tarayıcı açılmadı** — http://localhost:5001 adresine elle gidin.
- **"python not found"** — Python kurulu değil ya da PATH'e eklenmemiş;
  python.org'dan yeniden kurup tekrar deneyin.

---

## Maliyeti nedir

API çağrıları için doğrudan seçtiğiniz sağlayıcıya (Anthropic, OpenAI veya
Google) ödeme yaparsınız — hiçbir şey aradaki başka birinden geçmez.
Kitabın tek seferlik bütün-kitap okumasının ve her paragrafın maliyeti,
ilerledikçe size gösterilir. Tüm zamanların toplam harcamanızı, sağlayıcı
ve model kırılımıyla, sağ üstteki **Harcamalar** ekranından görebilirsiniz.
Tam boy bir roman, seçtiğiniz modele göre tipik olarak birkaç dolardan
düşük çift haneli rakamlara kadar tutar.
