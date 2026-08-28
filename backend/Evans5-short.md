# SOBOLEV SPACES

5.1 Hölder spaces   
5.2 Sobolev spaces   
5.3 Approximation   
5.4 Extensions   
5.5 Traces   
5.6 Sobolev inequalities   
5.7 Compactness   
5.8 Additional topics   
5.9 Other spaces of functions   
5.10 Problems   
5.11 References

This chapter mostly develops the theory of Sobolev spaces, which turn out often to be the proper setting in which to apply ideas of functional analysis to glean information concerning partial differential equations. The following material is often subtle, and will seem largely unmotivated, but ultimately will prove extremely useful.

Since we have in mind eventual applications to rather wide classes of partial differential equations, it is worth sketching out here our overall point of view. Our intention, broadly put, will be later to take various specific PDE and to recast them abstractly as operators acting on appropriate linear spaces. We can symbolically write this as

$$
A : X \to Y ,
$$

where the operator $A$ encodes the structure of the partial differential equations, including possibly boundary conditions, etc., and $X$ , $Y$ are spaces of functions. The great advantage is that once our PDE problem has been suitably interpreted in this form, we can often employ the general and elegant principles of functional analysis (Appendix D) to study the solvability of various equations involving $A$ . We will later see that the really hard work is not so much the invocation of functional analysis, but rather finding the "right” spaces $X$ , $Y$ and the“"right” abstract operators $A$ . Sobolev spaces are designed precisely to make all this work out properly, and so these are usually the proper choices for $X , Y$ .

We will utilize Sobolev spaces for studying linear elliptic, parabolic and hyperbolic PDE in Chapters $_ { 6 - 7 }$ , and for studying nonlinear eliptic and parabolic equations in Chapters 8–9 .

The reader may wish to look over some of the terminology for functional analysis in Appendix D before going further.

# 5.1. HÖLDER SPACES

Before turning to Sobolev spaces, we first discuss the simpler Hölder spaces.

Assume $U \subset \mathbb { R } ^ { n }$ is open and $0 < \gamma \leq 1$ . We have previously considered the class of Lipschitz continuous functions $u : U \to \mathbb { R }$ , which by definition satisfy the estimate

$$
| u ( x ) - u ( y ) | \leq C | x - y | \quad ( x , y \in U )
$$

for some constant $C$ . Now (1) of course implies $\pmb { u }$ is continuous, and more importantly provides a uniform modulus of continuity. It turns out to be useful to consider also functions $\textbf { \em u }$ satisfying a variant of (1), namely

$$
| u ( x ) - u ( y ) | \leq C | x - y | ^ { \gamma } \quad ( x , y \in U )
$$

for some constant $C$ . Such a function is said to be Hölder continuous with exponent $\gamma$ .

DEFINITIONS. (i) If $u : U \to \mathbb { R }$ is bounded and continuous, we write

$$
\| u \| _ { C ( { \bar { U } } ) } : = \operatorname* { s u p } _ { x \in U } | u ( x ) | .
$$

(i) The $\gamma ^ { t h }$ -Hölder seminorm of $u : U \to \mathbb { R }$ is

$$
[ u ] _ { { \cal C } ^ { 0 , \gamma } ( \bar { U } ) } : = \operatorname* { s u p } _ { \stackrel { x , y \in U } { x \neq y } } \left\{ \frac { \vert u ( x ) - u ( y ) \vert } { \vert x - y \vert ^ { \gamma } } \right\} ,
$$

and the $\gamma ^ { t h }$ -Hölder norm is

$$
\| u \| _ { C ^ { 0 , \gamma } ( \bar { U } ) } : = \| u \| _ { C ( \bar { U } ) } + [ u ] _ { C ^ { 0 , \gamma } ( \bar { U } ) } .
$$

DEFINITION. The Hölder space

$$
C ^ { k , \gamma } ( \bar { U } )
$$

consists of all functions $u \in C ^ { k } ( \hat { U } )$ for which the norm

$$
\| u \| _ { C ^ { k , \gamma } ( \bar { U } ) } : = \sum _ { | \alpha | \leq k } \| D ^ { \alpha } u \| _ { C ( \bar { U } ) } + \sum _ { | \alpha | = k } [ D ^ { \alpha } u ] _ { C ^ { 0 , \gamma } ( \bar { U } ) }
$$

is finite.

So the space $C ^ { k , \gamma } ( { \bar { U } } )$ consists of those functions $\textbf { \em u }$ that are $k \mathrm { . }$ -times continuously differentiable and whose $k ^ { t h }$ -partial derivatives are Hölder continuous with exponent $\gamma$ . Such functions are well-behaved, and furthermore the space $C ^ { k , \gamma } ( \bar { U } )$ itself possesses a good mathematical structure:

THEOREM 1 (Hölder spaces as function spaces). The space of functions $C ^ { k , \gamma } ( \bar { U } )$ is a Banach space.

The proof is left as an exercise (Problem 1), but let us pause here to make clear what is being asserted. Recall from $\ S \bf { D . 1 }$ that if $X$ denotes a real linear space, then a mapping $\begin{array} { r l } { \| } & { { } \| : X \to [ 0 , \infty ) } \end{array}$ is called a norm provided (i) $\| u + v \| \leq \| u \| + \| v \|$ for all $u , v \in X$ (i) $\| \lambda u \| = | \lambda | \| u \|$ for all $\boldsymbol { u } \in \boldsymbol { X }$ , $\lambda \in \mathbb { R }$ , (ini) $\lVert \boldsymbol { u } \rVert = 0$ if and only if $u = 0$

Anorpvideihantifency $\{ u _ { k } \} _ { k = 1 } ^ { \infty }$ $\subset X$ converges to $u \in X$ , written $u _ { k } \to u$ , if $\begin{array} { r } { \operatorname* { l i m } _ { k  \infty } \| u _ { k } - u \| = 0 } \end{array}$ . A Banach space is then a normed linear space which is complete, that is, within which each Cauchy sequence converges.

So in Theorem 1 we are stating that if we take on the linear space $C ^ { k , \gamma } ( \bar { U } )$ the norm $\| \cdot \| = \| \cdot \| _ { C ^ { k , \gamma } ( \bar { U } ) }$ , defined by (3), then $\| \cdot \|$ verifes properties (i)–(ii) above, and in addition each Cauchy sequence converges.

# 5.2. SOBOLEV SPACES

The Hölder spaces introduced in $\ S 5 . 1$ are unfortunately not often suitable settings for elementary PDE theory, as we usually cannot make good enough analytic estimates to demonstrate that the solutions we construct actually belong to such spaces. What are needed rather are some other kinds of spaces, containing less smooth functions. In practice we must strike a balance, by designing spaces comprising functions which have some, but not too great, smoothness properties.

# 5.2.1. Weak derivatives.

We start off by substantially weakening the notion of partial derivatives.

Notation. Let $C _ { c } ^ { \infty } ( U )$ denote the space of infinitely differentiable functions $\phi : U \to { \mathbb { R } }$ , with compact support in $U$ . We will call a function $\phi$ belonging to $C _ { c } ^ { \infty } ( U )$ a test function. □

Motivation for definition of weak derivative. Assume we are given a function $u ~ \in ~ C ^ { 1 } ( U )$ . Then if $\phi \in C _ { c } ^ { \infty } ( U )$ , we see from the integration by parts formula that

$$
\int _ { U } u \phi _ { x _ { i } } d x = - \int _ { U } u _ { x _ { i } } \phi d x \quad ( i = 1 , \ldots , n ) .
$$

There are no boundary terms, since $\phi$ has compact support in $U$ and thus vanishes near $\partial U$ . More generally now, if $k$ is a positive integer, $u \in C ^ { k } ( U )$ , and $\alpha = ( \alpha _ { 1 } , \ldots , \alpha _ { n } )$ is a multiindex of order $| \alpha | = \alpha _ { 1 } + \cdot \cdot \cdot + \alpha _ { n } = k$ , then

$$
\int _ { U } u D ^ { \alpha } \phi d x = ( - 1 ) ^ { | \alpha | } \int _ { U } D ^ { \alpha } u \phi d x .
$$

This equality holds since

$$
D ^ { \alpha } \phi = \frac { \partial ^ { \alpha _ { 1 } } } { \partial x _ { 1 } ^ { \alpha _ { 1 } } } \cdot \cdot \cdot \frac { \partial ^ { \alpha _ { n } } } { \partial x _ { n } ^ { \alpha _ { n } } } \phi
$$

and we can apply formula (1) $| \alpha |$ times.

We next examine formula (2), valid for $u \in C ^ { k } ( U )$ , and ask whether some variant of it might still be true even if $\textbf { \em u }$ is not $k$ times continuously differentiable. Now the left hand side of (2) makes sense if $\pmb { u }$ is only locally summable: the problem is rather that if $\textbf { \em u }$ is not $C ^ { k }$ , then the expression (cid:) $v ^ { \alpha } u ^ { \prime \prime }$ on the right hand side of (2) has no obvious meaning. We resolve this difficulty by asking if there exists a locally summable function $v$ for which formula (2) is valid, with $v$ replacing $D ^ { \alpha } u$ :

DEFINITION. Suppose $u , v \in L _ { \mathrm { l o c } } ^ { 1 } ( U )$ , and $\pmb { \alpha }$ is a multiindex. We say that $v$ is the $\alpha ^ { \mathrm { t } h }$ -weak partial derivative of $\textbf { \em u }$ , written

$$
D ^ { \alpha } u = v ,
$$

provided

$$
\int _ { U } u { \cal D } ^ { \alpha } \phi d x = ( - 1 ) ^ { | \alpha | } \int _ { U } v \phi d x
$$

for all test functions $\phi \in C _ { c } ^ { \infty } ( U )$ .

In other words, if we are given $\pmb { u }$ and if there happens to exist a function $v$ which verifies (3) for all $\phi$ , we say that $D ^ { \alpha } u = v$ in the weak sense. If there does not exist such a function v, then u does not possess a weak ath-partial derivative.

LEMMA (Uniqueness of weak derivatives). $A$ weak $\alpha ^ { t h }$ -partial derivative of $\textbf { \em u }$ , if it exists, is uniquely defined up to a set of measure zero.

Proof. Assume that $v , \tilde { v } \in L _ { \mathrm { l o c } } ^ { 1 } ( U )$ satisfy

$$
\int _ { U } u D ^ { \alpha } \phi d x = ( - 1 ) ^ { | \alpha | } \int _ { U } v \phi d x = ( - 1 ) ^ { | \alpha | } \int _ { U } { \tilde { v } } \phi d x
$$

for all $\phi \in C _ { c } ^ { \infty } ( U )$ . Then

$$
\int _ { U } ( v - \tilde { v } ) \phi d x = 0
$$

for all $\phi \in C _ { c } ^ { \infty } ( U )$ ; whence $v - \tilde { v } = 0$ a.e.

Example 1. Let $n = 1$ , $U = ( 0 , 2 )$ , and

$$
u ( x ) = { \left\{ \begin{array} { l l } { x \qquad { \mathrm { i f ~ } } 0 < x \leq 1 } \\ { 1 \qquad { \mathrm { i f ~ } } 1 \leq x < 2 . } \end{array} \right. }
$$

Define

$$
v ( x ) = { \left\{ \begin{array} { l l } { 1 \qquad { \mathrm { i f } } 0 < x \leq 1 } \\ { 0 \qquad { \mathrm { i f } } 1 < x < 2 . } \end{array} \right. }
$$

Let us show $u ^ { \prime } = v$ in the weak sense. To see this, choose any $\phi \in C _ { c } ^ { \infty } ( U )$ We must demonstrate

$$
\int _ { 0 } ^ { 2 } u \phi ^ { \prime } d x = - \int _ { 0 } ^ { 2 } v \phi d x .
$$

But we easily calculate

$$
\begin{array} { l } { { \displaystyle \int _ { 0 } ^ { 2 } u \phi ^ { \prime } d x = \int _ { 0 } ^ { 1 } x \phi ^ { \prime } d x + \int _ { 1 } ^ { 2 } \phi ^ { \prime } d x } } \\ { { \displaystyle \qquad = - \int _ { 0 } ^ { 1 } \phi d x + \phi ( 1 ) - \phi ( 1 ) = - \int _ { 0 } ^ { 2 } v \phi d x , } } \end{array}
$$

as required.

Example 2. Let $n = 1$ , $U = \left( 0 , 2 \right)$ , and

$$
u ( x ) = \left\{ { \begin{array} { l l } { x } & { { \mathrm { i f } } 0 < x \leq 1 } \\ { 2 } & { { \mathrm { i f } } 1 < x < 2 . } \end{array} } \right.
$$

We assert $\mathbf { \Omega } _ { \pmb { u } ^ { \prime } }$ does not exist in the weak sense. To check this, we must show there does not exist any function $v \in L _ { \mathrm { l o c } } ^ { 1 } ( U )$ satisfying

$$
\int _ { 0 } ^ { 2 } u \phi ^ { \prime } d x = - \int _ { 0 } ^ { 2 } v \phi d x
$$

for all $\phi \in C _ { c } ^ { \infty } ( U )$ . Suppose, to the contrary, (5) were valid for some $v$ and all $\phi$ . Then

$$
\begin{array} { c } { { \displaystyle - \int _ { 0 } ^ { 2 } v \phi d x = \int _ { 0 } ^ { 2 } u \phi ^ { \prime } d x = \int _ { 0 } ^ { 1 } x \phi ^ { \prime } d x + 2 \int _ { 1 } ^ { 2 } \phi ^ { \prime } d x } } \\ { { \displaystyle } } \\ { { \displaystyle = - \int _ { 0 } ^ { 1 } \phi d x - \phi ( 1 ) . } } \end{array}
$$

Choose a sequence $\{ \phi _ { m } \} _ { m = 1 } ^ { \infty }$ of smooth functions satisfying

$$
0 \leq \phi _ { m } \leq 1 , \phi _ { m } ( 1 ) = 1 , \phi _ { m } ( x )  0
$$

Replacing $\phi$ by $\phi _ { m }$ in (6) and sending $m \to \infty$ , we discover

$$
1 = \operatorname* { l i m } _ { m \to \infty } \phi _ { m } ( 1 ) = \operatorname* { l i m } _ { m \to \infty } \left[ \int _ { 0 } ^ { 2 } v \phi _ { m } d x - \int _ { 0 } ^ { 1 } \phi _ { m } d x \right] = 0 ,
$$

a contradiction.

More sophisticated examples appear in the next section.

# 5.2.2. Definition of Sobolev spaces.

Fix $1 \leq p \leq \infty$ and let $k$ be a nonnegative integer. We define now certain function spaces, whose members have weak derivatives of various orders lying in various $L ^ { p }$ spaces.

DEFINITION. The Sobolev space

$$
W ^ { k , \dot { p } } ( U )
$$

consists of all locally summable functions $u : U \to \mathbb { R }$ such that for each multiindex $\pmb { \alpha }$ with $| { \boldsymbol { \alpha } } | \leq k$ , $D ^ { \alpha } u$ exists in the weak sense and belongs to $L ^ { p } ( U )$ .

Remarks. (i) If $p = 2$ , we usually write

$$
H ^ { k } ( U ) = W ^ { k , 2 } ( U ) \quad ( k = 0 , 1 , \dots ) .
$$

The letter $H$ is used, since—as we will see $H ^ { k } ( U )$ is a Hilbert space. Note that $H ^ { 0 } ( U ) = L ^ { 2 } ( U )$ .

(ii) We henceforth identify functions in $W ^ { k , p } ( U )$ which agree a.e.

DEFINITION. If $u \in W ^ { k , p } ( U )$ , we define its norm to be

$$
\| u \| _ { W ^ { k , p } ( U ) } : = \left\{ \begin{array} { l l } { \left( \sum _ { | \alpha | \leq k } \int _ { U } | D ^ { \alpha } u | ^ { p } d x \right) ^ { 1 / p } } & { ( 1 \leq p < \infty ) } \\ { \sum _ { | \alpha | \leq k } \mathrm { e s s } \operatorname* { s u p } _ { U } | D ^ { \alpha } u | } & { ( p = \infty ) . } \end{array} \right.
$$

DEFINITIONS. (i) Let $\{ u _ { m } \} _ { m = 1 } ^ { \infty }$ , $u \in W ^ { k , p } ( U )$ . We say $u _ { m }$ converges to u in $W ^ { k , p } ( U )$ , written

$$
u _ { m }  u \quad i n W ^ { k , p } ( U ) ,
$$

provided

$$
\operatorname* { l i m } _ { m  \infty } \| u _ { m } - u \| _ { W ^ { k , p } ( U ) } = 0 .
$$

(ii) We write

$$
u _ { m }  u _ { \mathrm { ~ \tiny ~ \begin{array} { ~ 1 } { ~ \it ~ \cdot ~ } } \end{array} ~ \textstyle u ~ } u _ { \mathrm { \tiny ~ l o c } } ^ { k , p } ( U ) ,
$$

to mean

$$
u _ { m } \to u \quad i n W ^ { k , p } ( V )
$$

for each $V \subset \joinrel \subset U$ .

DEFINITION. We denote by

$$
W _ { 0 } ^ { k , p } ( U )
$$

the closure of $C _ { c } ^ { \infty } ( U )$ in $W ^ { k , p } ( U )$ .

Thus $u \in W _ { 0 } ^ { k , p } ( U )$ if and only if there exist functions $u _ { m } \in C _ { c } ^ { \infty } ( U )$ such that $u _ { m } \to u$ in $W ^ { k , p } ( U )$ . We interpret $W _ { 0 } ^ { k , p } ( U )$ as comprising those functions $u \in W ^ { k , p } ( U )$ such that

This will all be made clearer with the discussion of traces in $\ S 5 . 5$ .

Notation. It is customary to write

$$
H _ { 0 } ^ { k } ( U ) = W _ { 0 } ^ { k , 2 } ( U ) .
$$

We will see in the exercises that if $n = 1$ and $U$ is an open interval in $\mathbb { R } ^ { 1 }$ , then $u \ \in \ W ^ { 1 , p } ( U )$ if and only if $\pmb { u }$ equals a.e. an absolutely continuous function whose ordinary derivative (which exists a.e.) belongs to $L ^ { p } ( U )$ . Such a simple characterization is however only available for $n = 1$ . In general a function can belong to a Sobolev space, and yet be discontinuous and/or unbounded.

Example 3. Take $U = B ^ { 0 } ( 0 , 1 )$ , the open unit ball in $\mathbb { R } ^ { n }$ , and

$$
u ( x ) = | x | ^ { - \alpha } ( x \in U , \ x \neq 0 ) .
$$

For which values of $\alpha > 0 , n , p$ does $\textbf { \em u }$ belong to $W ^ { 1 , p } ( U ) ?$ To answer, note first $\textbf { \em u }$ is smooth away from 0, with

$$
u _ { x _ { i } } ( x ) = { \frac { - \alpha x _ { i } } { | x | ^ { \alpha + 2 } } } \quad ( x \neq 0 ) ,
$$

and so

$$
| D u ( x ) | = { \frac { | \alpha | } { | x | ^ { \alpha + 1 } } } \quad ( x \neq 0 ) .
$$

Let $\phi \in C _ { c } ^ { \infty } ( U )$ and fix $\varepsilon > 0$ . Then

$$
\int _ { U - B ( 0 , \varepsilon ) } u \phi _ { x _ { i } } d x = - \int _ { U - B ( 0 , \varepsilon ) } u _ { x _ { i } } \phi d x + \int _ { \partial B ( 0 , \varepsilon ) } u \phi \nu ^ { i } d S ,
$$

$\pmb \nu = ( \nu ^ { 1 } , \dots , \nu ^ { n } )$ denoting the inward pointing normal on $\partial B ( 0 , \varepsilon )$ . Now if $\alpha + 1 < n$ , $| D u ( x ) | \in L ^ { 1 } ( U )$ . In this case

$$
\left| \int _ { \partial B ( 0 , \varepsilon ) } u \phi \nu ^ { i } d S \right| \le \| \phi \| _ { L ^ { \infty } } \int _ { \partial B ( 0 , \varepsilon ) } \varepsilon ^ { - \alpha } d S \le C \varepsilon ^ { n - 1 - \alpha } \ \longrightarrow \ 0 .
$$

Thus

$$
\int _ { U } u \phi _ { x _ { i } } d x = - \int _ { U } u _ { x _ { i } } \phi d x
$$

for alll $\phi \in C _ { c } ^ { \infty } ( U )$ , provided $0 \leq \alpha < n - 1$ . Furthermore $\begin{array} { r } { | D u ( x ) | = \frac { \alpha } { | x | ^ { \alpha + 1 } } \in } \end{array}$ $L ^ { p } ( U )$ if and only if $( \alpha + 1 ) p < n$ . Consequently $u \in W ^ { 1 , p } ( U )$ if and only if $\begin{array} { r } { \alpha < \frac { n - p } { p } } \end{array}$ . In particular $u \not \in W ^ { 1 , p } ( U )$ for each ${ \pmb p } \geq n$ □

Example 4. Let $\{ r _ { k } \} _ { k = 1 } ^ { \infty }$ be a countable, dense subset of $U = B ^ { 0 } ( 0 , 1 )$ . Write

$$
u ( x ) = \sum _ { k = 1 } ^ { \infty } { \frac { 1 } { 2 ^ { k } } } | x - r _ { k } | ^ { - \alpha } \quad ( x \in U ) .
$$

Then $u \in W ^ { 1 , p } ( U )$ if and only if $\alpha < { \frac { n - p } { p } }$ . If $0 < \alpha < \frac { n - p } { p }$ , we see that $\pmb { u }$ belongs to $W ^ { 1 , p } ( U )$ and yet is unbounded on each open subset of $U$ .□

This last example illustrates a fundamental fact of life, that although a function $\textbf { \em u }$ belonging to a Sobolev space possesses certain smoothness properties, it can still be rather badly behaved in other ways.

# 5.2.3. Elementary properties.

Next we verify certain properties of weak derivatives. Note very carefully that whereas these various rules are obviously true for smooth functions, functions in Sobolev space are not necessarily smooth: we must always rely solely upon the definition of weak derivatives.

THEOREM 1 (Properties of weak derivatives). Assume $u , v \in W ^ { k , p } ( U )$ , $| \alpha | \leq k$ . Then

(i) $D ^ { \alpha } u \in \ W ^ { k - | \alpha | , p } ( U )$ and $D ^ { \beta } ( D ^ { \alpha } u ) ~ = ~ D ^ { \alpha } ( D ^ { \beta } u ) ~ = ~ D ^ { \alpha + \beta } u$ for all multiindices $\alpha , \beta$ with $| \alpha | + | \beta | \leq k$ .   
(ii) For each $\lambda , \mu \in \mathbb { R }$ , $\lambda u + \mu v \in W ^ { k , p } ( U )$ and $D ^ { \alpha } ( \lambda u + \mu v ) = \lambda D ^ { \alpha } u +$ $\mu D ^ { \alpha } v$ , $| { \boldsymbol { \alpha } } | \leq k$ .   
(ii) If $V$ is an open subset of $U$ , then $u \in W ^ { k , p } ( V )$ .   
(iv) I ${ \sf f } \in C _ { c } ^ { \infty } ( U )$ , then $\zeta u \in W ^ { k , p } ( U )$ and

$$
D ^ { \alpha } ( \zeta u ) = \sum _ { \beta \leq \alpha } { \binom { \alpha } { \beta } } D ^ { \beta } \zeta D ^ { \alpha - \beta } u \quad ( L e i b n i z ^ { \prime } f o r m u l a ) ,
$$

where (g) = β(α−β)1.

Proof. 1. To prove (i), first fx $\phi \in C _ { c } ^ { \infty } ( U )$ . Then $D ^ { \beta } \phi \in C _ { c } ^ { \infty } ( U )$ , and so

$$
\begin{array} { l } { \displaystyle \int _ { U } D ^ { \alpha } u D ^ { \beta } \phi d x = ( - 1 ) ^ { | \alpha | } \int _ { U } u D ^ { \alpha + \beta } \phi d x } \\ { \displaystyle \qquad = ( - 1 ) ^ { | \alpha | } ( - 1 ) ^ { | \alpha + \beta | } \int _ { U } D ^ { \alpha + \beta } u \phi d x } \\ { \displaystyle \qquad = ( - 1 ) ^ { | \beta | } \int _ { U } D ^ { \alpha + \beta } u \phi d x . } \end{array}
$$

Thus $D ^ { \beta } ( D ^ { \alpha } u ) = D ^ { \alpha + \beta } u$ in the weak sense.

2. Assertions (ii) and (ii) are easy, and the proofs are omitted.

3. We prove (7) by induction on $| \alpha |$ . Suppose first $| \alpha | = 1$ . Choose any $\phi \in C _ { c } ^ { \infty } ( U )$ . Then

$$
\begin{array} { r } { \displaystyle \int _ { U } \zeta u D ^ { \alpha } \phi d x = \displaystyle \int _ { U } u D ^ { \alpha } ( \zeta \phi ) - u ( D ^ { \alpha } \zeta ) \phi d x } \\ { \displaystyle = - \displaystyle \int _ { U } ( \zeta D ^ { \alpha } u + u D ^ { \alpha } \zeta ) \phi d x . } \end{array}
$$

Thus $D ^ { \alpha } ( \zeta u ) = \zeta D ^ { \alpha } u + u D ^ { \alpha } \zeta$ , as required.

Next assume $l < k$ and formula (7) is valid for all $| { \pmb { \alpha } } | \leq l$ and all functions $\zeta$ . Choose a multiindex $\pmb { \alpha }$ with $| \alpha | = l + 1$ . Then $\alpha = \beta + \gamma$ for some $\vert \beta \vert = l$ , $| \gamma | = 1$ . Then for $\phi$ as above,

$$
\begin{array} { l } { \displaystyle \int _ { U } \zeta u D ^ { \alpha } \phi d x = \int _ { U } \zeta u D ^ { \beta } ( D ^ { \gamma } \phi ) d x } \\ { \displaystyle \qquad = ( - 1 ) ^ { | \beta | } \int _ { U } \sum _ { \sigma \leq \beta } \binom { \beta } { \sigma } D ^ { \sigma } \zeta D ^ { \beta - \sigma } u D ^ { \gamma } \phi d x } \end{array}
$$

(by the induction assumption)

$$
= ( - 1 ) ^ { | \beta | + | \gamma | } \int _ { U } \sum _ { \sigma \leq \beta } { \binom { \beta } { \sigma } } D ^ { \gamma } ( D ^ { \sigma } \zeta D ^ { \beta - \sigma } u ) \phi d x
$$

(by the induction assumption again)

$$
= ( - 1 ) ^ { | \alpha | } \int _ { U } \sum _ { \sigma \leq \beta } { \binom { \beta } { \sigma } } \{ D ^ { \rho } \zeta D ^ { \alpha - \rho } u + D ^ { \sigma } \zeta D ^ { \alpha - \sigma } u \} \phi \ d x
$$

(where $\rho = \sigma + \gamma$ (d)

$$
= ( - 1 ) ^ { | \alpha | } \int _ { U } \left[ \sum _ { \sigma \leq \alpha } { \binom { \alpha } { \sigma } } D ^ { \sigma } \zeta D ^ { \alpha - \sigma } u ) \right] \phi \ d x ,
$$

since

$$
{ \binom { \beta } { \sigma - \gamma } } + { \binom { \beta } { \sigma } } = { \binom { \alpha } { \sigma } } .
$$

Not only do many of the usual rules of calculus apply to weak derivatives, but the Sobolev spaces themselves have a good mathematical structure:

THEOREM 2 (Sobolev spaces as function spaces). For each $k = 1 , \dots$ and $1 \leq p \leq \infty$ , the Sobolev space $W ^ { k , p } ( U )$ is a Banach space.

Proof. 1. Let us first of all check that $\| u \| _ { W ^ { k , p } ( U ) }$ is a norm. (See the discussion at the end of $\ S 5 . 1$ , or refer to $\ S \mathbf { D } . 1$ , for definitions.) Clearly

$$
\begin{array} { r } { \lVert \lambda u \rVert _ { W ^ { k , p } ( U ) } = \lvert \lambda \rvert \lVert u \rVert _ { W ^ { k , p } ( U ) } , } \end{array}
$$

and

Next assume $u , v \in W ^ { k , p } ( U )$ . Then if $1 \leq p < \infty$ , Minkowski's inequality (8B.2) implies

$$
\begin{array} { r l } & { \| u + v \| _ { \mathcal { W } ^ { \star , p } ( U ) } = \Bigg ( \displaystyle \sum _ { | \alpha | \leq k } \| D ^ { \alpha } u + D ^ { \alpha } v \| _ { L ^ { p } ( U ) } ^ { p } \Bigg ) ^ { 1 / p } } \\ & { \qquad \leq \Bigg ( \displaystyle \sum _ { | \alpha | \leq k } \left( \| D ^ { \alpha } u \| _ { L ^ { p } ( U ) } + \| D ^ { \alpha } v \| _ { L ^ { p } ( U ) } ) ^ { p } \right) ^ { 1 / p } } \\ & { \qquad \leq \Bigg ( \displaystyle \sum _ { | \alpha | \leq k } \| D ^ { \alpha } u \| _ { L ^ { p } ( U ) } ^ { p } \Bigg ) ^ { 1 / p } + \Bigg ( \displaystyle \sum _ { | \alpha | \leq k } \| D ^ { \alpha } v \| _ { L ^ { p } ( U ) } ^ { p } \Bigg ) ^ { 1 / p } } \\ & { \qquad = \| u \| _ { W ^ { \star , p } ( U ) } + \| v \| _ { W ^ { \star , p } ( U ) } . } \end{array}
$$

2. It remains to show that $W ^ { k , p } ( U )$ is complete. So assume $\{ u _ { m } \} _ { m = 1 } ^ { \infty }$ is a Cauchy sequence in $W ^ { k , p } ( U )$ . Then for each $| { \boldsymbol { \alpha } } | \leq k$ id: $\{ D ^ { \alpha } u _ { m } \} _ { m = 1 } ^ { \infty }$ Cauchy sequence in $L ^ { p } ( U )$ . Since $L ^ { p } ( U )$ is complete, there exist functions (cid:) $u _ { \alpha } \in L ^ { p } ( U )$ such that

$$
D ^ { \alpha } u _ { m }  u _ { \alpha } \mathrm { i n } \ L ^ { p } ( U )
$$

for each $| { \boldsymbol { \alpha } } | \leq k$ . In particular,

$$
u _ { m }  u _ { ( 0 , \ldots , 0 ) } = : u \mathrm { i n } \ L ^ { p } ( U ) .
$$

3. We now claim

$$
u \in W ^ { k , p } ( U ) , D ^ { \alpha } u = u _ { \alpha } \quad ( | \alpha | \leq k ) .
$$

To verify this assertion, fix $\phi \in C _ { c } ^ { \infty } ( U )$ . Then

$$
\begin{array} { l } { { \displaystyle \int _ { U } u D ^ { \alpha } \phi d x = \operatorname* { l i m } _ { m \to \infty } \int _ { U } u _ { m } D ^ { \alpha } \phi d x } } \\ { { \displaystyle \qquad = \operatorname* { l i m } _ { m \to \infty } ( - 1 ) ^ { | \alpha | } \int _ { U } D ^ { \alpha } u _ { m } \phi d x } } \\ { { \displaystyle \qquad = ( - 1 ) ^ { | \alpha | } \int _ { U } u _ { \alpha } \phi d x . } } \end{array}
$$

Thus (8) is valid. Since therefore $D ^ { \alpha } u _ { m }  D ^ { \alpha } u$ in $L ^ { p } ( U )$ for all $| { \boldsymbol { \alpha } } | \leq k$ we see that $u _ { m } \to u$ in $W ^ { k , p } ( U )$ , as required. □